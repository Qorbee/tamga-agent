"use client";

import {
  ArrowLeft01Icon,
  Attachment01Icon,
  Cancel01Icon,
  Forward02Icon,
  Globe02Icon,
  PlusSignIcon,
  SentIcon,
  TickDouble02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, use, useEffect, useState, type ReactNode } from "react";

import { CanvasLink, useCanvasView } from "@/components/canvas-link";
import { Hint } from "@/components/hint";
import { IconButton } from "@/components/icon-button";
import { ScenarioPicker } from "@/components/scenario-picker";
import { Segmented } from "@/components/segmented";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { getMe, getMessage, getPolicy, getWorld, listMessages, sayAs, skipDays, type MessageSummary, type Persona } from "@/lib/api";
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
 * The world beside the page, a column of its own between the page and the canvas, so a record
 * opened in the canvas never hides it: the scenario's people as a phone shows them, on WhatsApp
 * and by mail. A chat opens as its thread; a mail opens in the canvas beside it. What the judge
 * types arrives as that person's message, through the same intake as theirs.
 */
export function WorldPanel() {
  const { t } = useI18n();
  const sandbox = useSandbox();
  const world = useWorld(sandbox);
  const { open, setOpen } = use(WorldOpen);
  const canvas = useCanvasView();
  const personas = world.data?.personas ?? [];
  const [medium, setMedium] = useState<Persona["medium"]>("whatsapp");
  const [chosen, setChosen] = useState<string | null>(null);
  if (!sandbox || !open || personas.length === 0) return null;
  const shown = personas.filter((persona) => persona.medium === medium);
  const persona = shown.find((item) => item.key === chosen) ?? null;
  const now = world.data?.now ?? new Date().toISOString();
  // On a phone the world covers the app: it steps aside for the mail or file it opened.
  const stepAside = () => {
    if (!window.matchMedia("(min-width: 64rem)").matches) setOpen(false);
  };

  return (
    <aside
      data-slot="world-panel"
      aria-label={t.playground.world}
      className={cn(
        "bg-card flex min-h-0 flex-col overflow-hidden",
        "max-lg:fixed max-lg:inset-0 max-lg:z-50",
        "lg:border-border lg:m-2 lg:ms-0 lg:shrink-0 lg:rounded-2xl lg:border",
        canvas !== null ? "lg:w-72 2xl:w-80" : "lg:w-80",
      )}
    >
      <header className="border-border flex h-14 shrink-0 items-center gap-2 border-b ps-4 pe-2">
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium">{t.playground.world}</span>
          <You />
        </span>
        <Hint label={t.playground.worldHint}>
          <span tabIndex={0} className="text-muted-foreground bg-secondary rounded-full px-2 py-0.5 text-[11px]">
            {t.playground.badge}
          </span>
        </Hint>
        <IconButton icon={Cancel01Icon} label={t.playground.closeWorld} onClick={() => setOpen(false)} />
      </header>
      <div className="border-border border-b px-3 py-2">
        <Segmented
          fill
          label={t.playground.world}
          value={medium}
          onChange={(value) => {
            setMedium(value);
            setChosen(null);
          }}
          options={(["whatsapp", "mail"] as const).map((value) => ({ value, label: t.playground[value] }))}
        />
      </div>
      {persona === null ? (
        <PersonaList personas={shown} now={now} onPick={(key) => setChosen(key)} />
      ) : persona.medium === "whatsapp" ? (
        <ChatThread key={persona.key} persona={persona} now={now} onBack={() => setChosen(null)} onOpened={stepAside} />
      ) : (
        <MailThreadList key={persona.key} persona={persona} now={now} onBack={() => setChosen(null)} onOpened={stepAside} />
      )}
    </aside>
  );
}

/** The calm tints a persona's initial sits on, chosen by their key so each keeps theirs. */
const TINTS = [
  "bg-success-soft text-success-foreground",
  "bg-warning-soft text-warning-foreground",
  "bg-danger-soft text-danger-foreground",
  "bg-secondary text-foreground",
];

function Avatar({ persona, size = "size-10" }: { persona: Persona; size?: string }) {
  const tint = TINTS[[...persona.key].reduce((sum, ch) => sum + ch.charCodeAt(0), 0) % TINTS.length];
  const initials = persona.name
    .split(" ")
    .slice(0, 2)
    .map((part) => part.slice(0, 1))
    .join("");
  return (
    <span aria-hidden className={cn("flex shrink-0 items-center justify-center rounded-full text-sm font-medium", size, tint)}>
      {initials}
    </span>
  );
}

function useThread(persona: Persona) {
  return useQuery({
    queryKey: ["world-messages", persona.thread_key],
    queryFn: () => listMessages({ channel: "simulated", thread_key: persona.thread_key, limit: 100 }),
  });
}

/** Where the viewer's last read of a persona's thread is kept: per sandbox, since every sandbox's
 * personas share their thread keys and a skipped clock would mark a fresh sandbox's messages read. */
function useSeenKey(persona: Persona): string | null {
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  return me.data ? `tamga.world.seen.${me.data.company.id}.${persona.thread_key}` : null;
}

/** When the viewer last read a thread: this browser only, and nothing breaks without it. */
function seenAt(key: string | null): string | null {
  if (!key) return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function markSeen(key: string | null, at: string): void {
  if (!key) return;
  try {
    window.localStorage.setItem(key, at);
  } catch {
    // A private window keeps nothing: every thread stays unread there.
  }
}

/** A time for a list row: the hour today, "Yesterday", else the day. */
function useWhen(now: string) {
  const { t, locale } = useI18n();
  const today = localDay(now);
  return (sent: string) => {
    const day = formatDate(new Date(sent), locale, "day");
    if (sameDay(sent, today)) return formatDate(new Date(sent), locale, "time");
    if (sameDay(sent, shiftDay(today, -1))) return t.playground.yesterday;
    return day;
  };
}

// Both sides of a day's comparison in the browser's timezone, as each message's time is shown.
function localDay(at: string): string {
  const date = new Date(at);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function sameDay(sent: string, day: string): boolean {
  return localDay(sent) === day;
}

function shiftDay(day: string, by: number): string {
  const [year = 1970, month = 1, date = 1] = day.split("-").map(Number);
  const shifted = new Date(year, month - 1, date + by);
  return `${shifted.getFullYear()}-${String(shifted.getMonth() + 1).padStart(2, "0")}-${String(shifted.getDate()).padStart(2, "0")}`;
}

function PersonaList({ personas, now, onPick }: { personas: Persona[]; now: string; onPick: (key: string) => void }) {
  return (
    <ul className="min-h-0 flex-1 overflow-y-auto py-1">
      {personas.map((persona) => (
        <li key={persona.key}>
          <PersonaRow persona={persona} now={now} onPick={() => onPick(persona.key)} />
        </li>
      ))}
    </ul>
  );
}

function PersonaRow({ persona, now, onPick }: { persona: Persona; now: string; onPick: () => void }) {
  const { t } = useI18n();
  const thread = useThread(persona);
  const when = useWhen(now);
  const last = thread.data?.items[0];
  const seenKey = useSeenKey(persona);
  const seen = seenAt(seenKey);
  // Only what they said: the company's own messages are never unread to it.
  const unread = (thread.data?.items ?? []).filter((message) => message.direction === "inbound" && (!seen || message.sent_at > seen)).length;
  return (
    <button type="button" onClick={onPick} className="hover:bg-accent/50 flex w-full items-center gap-3 px-3 py-2.5 text-start">
      <Avatar persona={persona} />
      <span className="min-w-0 flex-1">
        <span className="flex items-baseline gap-2">
          <span className="min-w-0 truncate text-sm font-medium">{persona.name}</span>
          <span className="bg-secondary text-muted-foreground shrink-0 rounded-full px-1.5 py-px text-[10px]">
            {t.playground.relations[persona.relation]}
          </span>
          <span className="flex-1" />
          {last ? (
            <span className={cn("shrink-0 text-[11px] tabular-nums", unread > 0 ? "text-success-foreground font-medium" : "text-muted-foreground")}>
              {when(last.sent_at)}
            </span>
          ) : null}
        </span>
        <span className="flex items-center gap-2">
          <span className="text-muted-foreground min-w-0 flex-1 truncate text-xs">
            {last?.direction === "outbound" ? `${t.playground.you}: ` : null}
            {persona.medium === "mail" ? (last?.subject ?? persona.subject) : (last?.snippet ?? persona.role)}
          </span>
          {unread > 0 ? (
            <span className="bg-success text-card flex h-4.5 min-w-4.5 shrink-0 items-center justify-center rounded-full px-1 text-[10px] font-semibold tabular-nums">
              <span aria-hidden>{unread}</span>
              <span className="sr-only">{t.playground.unread(unread)}</span>
            </span>
          ) : null}
        </span>
      </span>
    </button>
  );
}

function ThreadHeader({ persona, onBack }: { persona: Persona; onBack: () => void }) {
  const { t } = useI18n();
  return (
    <div className="border-border flex shrink-0 items-center gap-2 border-b px-2 py-2">
      <IconButton icon={ArrowLeft01Icon} label={t.playground.back} onClick={onBack} />
      <Avatar persona={persona} size="size-9" />
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium">{persona.name}</span>
        <span className="text-muted-foreground block truncate text-xs">{persona.role}</span>
      </span>
    </div>
  );
}

/** A WhatsApp thread as a phone shows it: the wallpaper, a chip for each day, the persona's
 * bubbles with their time inside, oldest first, and a box to type as them. */
function ChatThread({ persona, now, onBack, onOpened }: { persona: Persona; now: string; onBack: () => void; onOpened: () => void }) {
  const { t, locale } = useI18n();
  const messages = useThread(persona);
  const said = [...(messages.data?.items ?? [])].reverse();
  const latest = said.at(-1)?.sent_at;
  const seenKey = useSeenKey(persona);
  useEffect(() => {
    if (latest) markSeen(seenKey, latest);
  }, [seenKey, latest]);
  const today = localDay(now);
  const dayOf = (sent: string) =>
    sameDay(sent, today)
      ? t.playground.today
      : sameDay(sent, shiftDay(today, -1))
        ? t.playground.yesterday
        : formatDate(new Date(sent), locale, "day");

  return (
    <>
      <ThreadHeader persona={persona} onBack={onBack} />
      <ol
        className="bg-world-wall flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto bg-(image:--world-pattern) bg-size-[160px] px-3 py-3"
        aria-live="polite"
      >
        {said.length === 0 && !messages.isPending ? (
          <li className="bg-card text-muted-foreground m-auto max-w-[24ch] rounded-lg px-3 py-2 text-center text-xs text-pretty">
            {t.playground.noMessages}
          </li>
        ) : null}
        {said.map((message, index) => {
          const day = dayOf(message.sent_at);
          const newDay = index === 0 || dayOf(said[index - 1]!.sent_at) !== day;
          return (
            <li key={message.id} className="grid gap-1">
              {newDay ? (
                <span className="bg-card text-muted-foreground my-1.5 justify-self-center rounded-lg px-2.5 py-1 text-[11px] font-medium shadow-xs">
                  {day}
                </span>
              ) : null}
              <Bubble message={message} first={newDay || said[index - 1]?.direction !== message.direction} onOpened={onOpened} />
            </li>
          );
        })}
      </ol>
      <SayBox persona={persona} rows={1} />
    </>
  );
}

/** Who the judge is in their sandbox, so no one in the World is mistaken for them. */
function You() {
  const { t } = useI18n();
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  if (!me.data) return null;
  const name = me.data.user.name;
  return (
    <span className="text-muted-foreground block truncate text-[11px]">
      {t.playground.youAre(name ?? t.playground.you, me.data.company.name)}
    </span>
  );
}

/** One message as a phone shows it: theirs on the left in white, yours on the right in green,
 * a tail on the first of a run, its files as chips that open, the time inside. */
function Bubble({ message, first, onOpened }: { message: MessageSummary; first: boolean; onOpened: () => void }) {
  const { locale } = useI18n();
  const ours = message.direction === "outbound";
  const tone = ours ? "bg-world-bubble-out" : "bg-world-bubble";
  return (
    <div
      className={cn(
        "relative max-w-[85%] rounded-lg px-2.5 pt-1.5 pb-1 shadow-xs",
        tone,
        ours ? "justify-self-end" : "justify-self-start",
        first && (ours ? "rounded-se-none" : "rounded-ss-none"),
      )}
    >
      {first ? (
        <span
          aria-hidden
          className={cn(
            "absolute top-0 size-2.5",
            tone,
            ours ? "-end-1.5 [clip-path:polygon(0_0,100%_0,0_100%)]" : "-start-1.5 [clip-path:polygon(100%_0,0_0,100%_100%)]",
          )}
        />
      ) : null}
      {message.attachment_count > 0 ? <Files message={message} onOpened={onOpened} /> : null}
      <p className="text-sm text-pretty whitespace-pre-line">
        {message.snippet}
        {/* Room for the time on the last line, as a phone keeps it. */}
        <span aria-hidden className="inline-block w-14" />
      </p>
      <span className="text-muted-foreground absolute end-2 bottom-1 flex items-center gap-0.5 text-[10px] tabular-nums">
        {formatDate(new Date(message.sent_at), locale, "time")}
        {ours ? <HugeiconsIcon icon={TickDouble02Icon} strokeWidth={2} className="size-3" aria-hidden /> : null}
      </span>
    </div>
  );
}

/** A message's files by their names, each opening in the canvas beside the World. */
function Files({ message, onOpened }: { message: MessageSummary; onOpened: () => void }) {
  const { t } = useI18n();
  const full = useQuery({ queryKey: ["message", message.id], queryFn: () => getMessage(message.id) });
  const files = (full.data?.attachments ?? []).filter((file) => !file.inline);
  if (files.length === 0)
    return (
      <span className="bg-muted text-muted-foreground mb-1 flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs">
        <HugeiconsIcon icon={Attachment01Icon} className="size-3.5" aria-hidden />
        {t.playground.attachment}
      </span>
    );
  return (
    <span className="mb-1 grid gap-1">
      {files.map((file) => (
        <CanvasLink
          key={file.document_id}
          view={{ kind: "document", id: file.document_id }}
          onClick={onOpened}
          className="bg-muted hover:bg-accent flex min-w-0 items-center gap-1.5 rounded-md px-2 py-1.5 text-xs"
        >
          <HugeiconsIcon icon={Attachment01Icon} className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
          <span className="truncate">{file.filename}</span>
        </CanvasLink>
      ))}
    </span>
  );
}

/** A mail persona's thread: each mail by its subject and first lines, opened whole in the
 * canvas, and a box to write the next one as them. */
function MailThreadList({
  persona,
  now,
  onBack,
  onOpened,
}: {
  persona: Persona;
  now: string;
  onBack: () => void;
  onOpened: () => void;
}) {
  const { t } = useI18n();
  const messages = useThread(persona);
  const when = useWhen(now);
  const mails = messages.data?.items ?? [];
  const latest = mails[0]?.sent_at;
  const seenKey = useSeenKey(persona);
  useEffect(() => {
    if (latest) markSeen(seenKey, latest);
  }, [seenKey, latest]);
  return (
    <>
      <ThreadHeader persona={persona} onBack={onBack} />
      <ul className="divide-border min-h-0 flex-1 divide-y overflow-y-auto">
        {mails.length === 0 && !messages.isPending ? (
          <li className="text-muted-foreground px-4 py-6 text-center text-xs">{t.playground.noMessages}</li>
        ) : null}
        {mails.map((mail) => (
          <li key={mail.id}>
            <CanvasLink
              view={{ kind: "mail", id: mail.id }}
              onClick={onOpened}
              className="hover:bg-accent/50 aria-[current=true]:bg-secondary block px-4 py-3"
            >
              <span className="flex items-baseline gap-2">
                <span className="min-w-0 flex-1 truncate text-sm font-medium">
                  {mail.direction === "outbound" ? `${t.playground.you}: ` : null}
                  {mail.subject ?? persona.subject}
                </span>
                <span className="text-muted-foreground shrink-0 text-[11px] tabular-nums">{when(mail.sent_at)}</span>
              </span>
              <span className="text-muted-foreground mt-0.5 line-clamp-2 text-xs">{mail.snippet}</span>
              {mail.attachment_count > 0 ? (
                <span className="text-muted-foreground mt-1 flex items-center gap-1 text-[11px]">
                  <HugeiconsIcon icon={Attachment01Icon} className="size-3" aria-hidden />
                  {t.playground.attachment}
                </span>
              ) : null}
            </CanvasLink>
          </li>
        ))}
      </ul>
      <SayBox persona={persona} rows={3} />
    </>
  );
}

/** Types as the persona: a chat line, or a mail's text under their subject. */
function SayBox({ persona, rows }: { persona: Persona; rows: number }) {
  const { t } = useI18n();
  const [text, setText] = useState("");
  const queryClient = useQueryClient();
  const send = useMutation({
    mutationFn: sayAs,
    onSuccess: () => {
      setText("");
      void queryClient.invalidateQueries({ queryKey: ["world-messages", persona.thread_key] });
    },
  });
  const most = persona.medium === "mail" ? 1000 : 200;
  const submit = () => {
    const typed = text.trim();
    if (typed && !send.isPending) send.mutate({ persona: persona.key, text: typed });
  };
  return (
    <form
      className={cn("border-border flex items-end gap-2 border-t p-2.5", persona.medium === "whatsapp" && "bg-world-wall")}
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <Textarea
        value={text}
        rows={rows}
        maxLength={most}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          // A chat line goes on Enter, as on a phone; a mail keeps its new lines.
          if (persona.medium === "whatsapp" && event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
        placeholder={t.playground.writeAs(persona.name.split(" ")[0] ?? persona.name)}
        aria-describedby="world-typed"
        className={cn(
          "bg-card min-h-10 min-w-0 flex-1 resize-none [field-sizing:fixed]",
          persona.medium === "whatsapp" ? "rounded-3xl px-4 py-2.5" : "rounded-xl",
        )}
      />
      <IconButton
        icon={SentIcon}
        label={t.playground.send}
        type="submit"
        variant="default"
        className="size-10 shrink-0 rounded-full"
        disabled={!text.trim() || send.isPending}
      />
      <span id="world-typed" className="sr-only">
        {t.playground.sent}
      </span>
    </form>
  );
}
