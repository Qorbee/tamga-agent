"use client";

import { MoreHorizontalIcon, SentIcon, Tick02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Hint } from "@/components/hint";
import { IconButton } from "@/components/icon-button";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  ApiError,
  changeMemory,
  changeMemoryEntry,
  discardMemoryEntry,
  getMemoryEntry,
  getParty,
  getProject,
  listAllMemory,
  type MemoryChanges as Changes,
  type MemoryEntry,
  type MemoryKind,
} from "@/lib/api";
import { formatAgo } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { useAsks, useIsOwner } from "@/lib/use-role";
import { cn } from "@/lib/utils";

const KINDS: MemoryKind[] = ["person", "alias", "term", "practice", "preference"];
/** The longest text one message to the memorist reads (`MemoryText`). */
const TOLD = 2000;

/** Everything that shows an entry reads it again after a change. */
function useRefresh() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["memory"] });
  };
}

/** What a message to the memorist asks: what was written, and the entry an edit is about. */
function useTell(onDone: (changes: Changes) => void) {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: ({ text, entryId }: { text: string; entryId?: string }) =>
      changeMemory({ text, entry_id: entryId ?? null }),
    onSuccess: onDone,
    onSettled: refresh,
  });
}

function useWhy(error: Error | null): string {
  const { t } = useI18n();
  return error instanceof ApiError && error.status === 429 ? t.memory.spent : t.memory.failed;
}

/**
 * One thing Tamga knows (ADR-0024, ADR-0030), as Claude's memory lists it: who or what it is
 * about, its sentence, and when it last changed. The memorist writes every entry; the owner edits
 * one by telling it what should change, retires it (and uses it again), and deletes one retired
 * for good. A draft from a chat or from ops waits for the owner's Confirm.
 */
export function MemoryRow({ entry, fresh = false }: { entry: MemoryEntry; fresh?: boolean }) {
  const { t, locale } = useI18n();
  const words = t.memory;
  const owner = useIsOwner();
  const refresh = useRefresh();
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const change = useMutation({
    mutationFn: (body: Parameters<typeof changeMemoryEntry>[1]) => changeMemoryEntry(entry.id, body),
    onSettled: refresh,
  });
  const discard = useMutation({
    mutationFn: () => discardMemoryEntry(entry.id),
    onSuccess: () => setDeleting(false),
    onSettled: refresh,
  });
  const busy = change.isPending || discard.isPending;
  const draft = entry.state === "proposed";
  const retired = entry.state === "retired";
  const ago = formatAgo(new Date(entry.updated_at), locale);

  return (
    <div data-slot="memory-entry" className="grid gap-2 py-3">
      <div className="flex items-start gap-3">
        <div className="grid min-w-0 flex-1 gap-x-6 gap-y-0.5 sm:grid-cols-[minmax(0,11rem)_minmax(0,1fr)_auto]">
          <p className={cn("flex min-w-0 items-center gap-2 text-sm font-medium", retired && "text-muted-foreground")}>
            <span className="truncate">{entry.title ?? words.kinds[entry.kind]}</span>
            {fresh ? (
              <span className="bg-primary text-primary-foreground shrink-0 rounded-md px-1.5 py-px text-[11px] font-medium">
                {words.new}
              </span>
            ) : null}
          </p>
          <div className="min-w-0">
            <p className={cn("text-sm text-pretty break-words", retired && "text-muted-foreground line-through")}>
              {entry.text}
            </p>
            <Subject entry={entry} />
          </div>
          <p className="text-muted-foreground text-xs whitespace-nowrap sm:pt-0.5">
            {words.updated(ago ?? words.justNow)}
          </p>
        </div>
        {owner && !draft && !editing ? (
          <DropdownMenu modal={false}>
            <Hint label={words.more}>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon-sm" className="text-muted-foreground -me-2 -mt-1" aria-label={words.more} disabled={busy}>
                  <HugeiconsIcon icon={MoreHorizontalIcon} strokeWidth={1.75} />
                </Button>
              </DropdownMenuTrigger>
            </Hint>
            <DropdownMenuContent align="end">
              {retired ? (
                <>
                  <DropdownMenuItem onSelect={() => change.mutate({ state: "held" })}>{words.restore}</DropdownMenuItem>
                  <DropdownMenuItem variant="destructive" onSelect={() => setDeleting(true)}>
                    {words.delete}
                  </DropdownMenuItem>
                </>
              ) : (
                <>
                  <DropdownMenuItem onSelect={() => setEditing(true)}>{words.edit}</DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => change.mutate({ state: "retired" })}>{words.retire}</DropdownMenuItem>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
      </div>
      {editing ? <EditBox entry={entry} onClose={() => setEditing(false)} /> : null}
      {owner && draft && !editing ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" disabled={busy} onClick={() => change.mutate({ state: "held" })}>
            {words.confirm}
          </Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => setEditing(true)}>
            {words.edit}
          </Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => discard.mutate()}>
            {words.discard}
          </Button>
        </div>
      ) : null}
      {!owner && draft ? <p className="text-muted-foreground text-xs">{words.ownerConfirms}</p> : null}
      {change.isError || discard.isError ? (
        <p className="text-danger-foreground text-xs" role="alert">
          {t.errors.generic}
        </p>
      ) : null}
      <Dialog open={deleting} onOpenChange={setDeleting}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{words.deleteTitle}</DialogTitle>
            <DialogDescription>{words.deleteBody}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDeleting(false)}>
              {words.cancel}
            </Button>
            <Button variant="destructive" disabled={discard.isPending} onClick={() => discard.mutate()}>
              {words.delete}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** An edit is what should change, told to the memorist with the entry (ADR-0030). */
function EditBox({ entry, onClose }: { entry: MemoryEntry; onClose: () => void }) {
  const { t } = useI18n();
  const words = t.memory;
  const [text, setText] = useState("");
  const [why, setWhy] = useState("");
  const tell = useTell((done) => {
    if (done.changes.length) onClose();
    else setWhy(done.reason);
  });
  const failed = useWhy(tell.error);
  const told = text.trim();
  return (
    <form
      className="grid gap-1.5"
      onSubmit={(event) => {
        event.preventDefault();
        if (told) tell.mutate({ text: told, entryId: entry.id });
      }}
    >
      <Composer
        value={text}
        onChange={setText}
        placeholder={words.whatChanges}
        busy={tell.isPending}
        onCancel={onClose}
      />
      <Status pending={tell.isPending} changes={null} why={why} error={tell.isError ? failed : ""} />
    </form>
  );
}

/** The line above a box: the memorist at work, what it changed, or why it changed nothing. */
function Status({
  pending,
  changes,
  why,
  error,
}: {
  pending: boolean;
  changes: Changes["changes"] | null;
  why: string;
  error: string;
}) {
  const { t } = useI18n();
  const words = t.memory;
  const [open, setOpen] = useState(false);
  if (pending)
    return (
      <p className="shimmer text-muted-foreground px-1 text-sm motion-reduce:animate-none" role="status">
        {words.looking}
      </p>
    );
  if (error)
    return (
      <p className="text-danger-foreground px-1 text-sm" role="alert">
        {error}
      </p>
    );
  if (!why) return null;
  const [first] = changes ?? [];
  const said =
    changes === null || changes.length === 0 ? (
      <span>{words.noChange}</span>
    ) : (
      <span className="flex min-w-0 items-center gap-2">
        <HugeiconsIcon icon={Tick02Icon} strokeWidth={2} className="size-4 shrink-0" aria-hidden />
        {changes.length === 1 && first ? (
          <span className="min-w-0 truncate">
            {words.done[first.action]} <span className="text-foreground">{words.sections[first.entry.kind]}</span>
            {" › "}
            <span className="text-foreground">{first.entry.title ?? words.kinds[first.entry.kind]}</span>
          </span>
        ) : (
          <span>{words.changes(changes.length)}</span>
        )}
      </span>
    );
  return (
    <div className="text-muted-foreground grid min-w-0 gap-1 px-1 text-sm" role="status">
      <div className="flex min-w-0 items-center justify-between gap-3">
        {said}
        <Button type="button" size="xs" variant="ghost" className="text-muted-foreground shrink-0" onClick={() => setOpen(!open)}>
          {open ? words.hide : words.seeWhy}
        </Button>
      </div>
      {open ? <p className="text-pretty">{why}</p> : null}
    </div>
  );
}

/** A box like the chat's: Enter sends, Shift+Enter starts a new line. */
function Composer({
  value,
  onChange,
  placeholder,
  busy,
  onCancel,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  busy: boolean;
  onCancel?: () => void;
}) {
  const { t } = useI18n();
  return (
    <div className="border-border bg-card focus-within:border-ring flex min-w-0 items-end gap-2 rounded-3xl border py-1.5 ps-4 pe-1.5 transition-colors">
      <Textarea
        value={value}
        maxLength={TOLD}
        rows={1}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            event.currentTarget.form?.requestSubmit();
          }
          if (event.key === "Escape" && onCancel) onCancel();
        }}
        placeholder={placeholder}
        aria-label={placeholder}
        // As wide as the box leaves it, never as wide as its placeholder.
        className="max-h-40 min-h-8 w-full min-w-0 flex-1 resize-none border-0 bg-transparent px-0 py-1.5 text-sm shadow-none [field-sizing:fixed] focus-visible:ring-0 dark:bg-transparent"
      />
      {onCancel ? (
        <Button type="button" size="sm" variant="ghost" className="shrink-0" onClick={onCancel}>
          {t.memory.cancel}
        </Button>
      ) : null}
      <IconButton
        icon={SentIcon}
        label={t.memory.send}
        type="submit"
        variant="default"
        className="shrink-0 rounded-full"
        disabled={!value.trim() || busy}
      />
    </div>
  );
}

/** The firm or the project an entry is about, by its name. */
function Subject({ entry }: { entry: MemoryEntry }) {
  const party = useQuery({
    queryKey: ["party", entry.party_id],
    queryFn: () => getParty(entry.party_id ?? ""),
    enabled: entry.party_id !== null,
    retry: false,
  });
  const project = useQuery({
    queryKey: ["project", entry.project_id],
    queryFn: () => getProject(entry.project_id ?? ""),
    enabled: entry.project_id !== null,
    retry: false,
  });
  const about = [party.data?.name, project.data ? `${project.data.number} · ${project.data.name}` : undefined].filter(Boolean);
  return about.length ? <p className="text-muted-foreground truncate text-xs">{about.join(" · ")}</p> : null;
}

/**
 * Settings → What Tamga knows, laid out as Claude's memory: the drafts that wait for the owner,
 * then what holds, grouped by kind, and what was retired on request. At the bottom, a box where
 * the owner (or ops) tells Tamga what to remember, change or forget in their own words: the
 * memorist reads the memory and changes it (`POST /memory/changes`, ADR-0030), and the line
 * above the box says what it did, or why it did nothing.
 */
export function MemorySection() {
  const { t } = useI18n();
  const words = t.memory;
  const owner = useIsOwner();
  const asks = useAsks();
  const refresh = useRefresh();
  const [retired, setRetired] = useState(false);
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const states = retired ? (["retired"] as const) : (["proposed", "held"] as const);
  const entries = useQuery({
    queryKey: ["memory", { states }],
    queryFn: () => listAllMemory({ state: [...states], sort: "-updated_at" }),
  });
  const drafts = (entries.data ?? []).filter((entry) => entry.state === "proposed");
  const shown = (entries.data ?? []).filter((entry) => entry.state !== "proposed");
  const confirmAll = useMutation({
    mutationFn: () => Promise.all(drafts.map((entry) => changeMemoryEntry(entry.id, { state: "held" }))),
    onSettled: refresh,
  });

  return (
    <section className="flex flex-col gap-4">
      <header>
        <div className="flex items-center justify-between gap-2 pb-1">
          <h3 className="text-muted-foreground text-xs font-medium">{words.title}</h3>
          <Button size="xs" variant="ghost" className="text-muted-foreground -me-2" onClick={() => setRetired(!retired)}>
            {retired ? words.showHeld : words.showRetired}
          </Button>
        </div>
        <p className="text-muted-foreground text-xs text-pretty">{words.hint}</p>
      </header>
      {drafts.length ? (
        <div className="border-border bg-card rounded-2xl border px-4 pt-2.5 pb-1">
          <div className="flex items-center justify-between gap-2">
            <h4 className="text-warning-foreground text-xs font-medium">{words.waitsCount(drafts.length)}</h4>
            {owner && drafts.length > 1 ? (
              <Button size="xs" variant="ghost" disabled={confirmAll.isPending} onClick={() => confirmAll.mutate()}>
                {words.confirmAll}
              </Button>
            ) : null}
          </div>
          <div className="divide-border divide-y">
            {drafts.map((entry) => (
              <MemoryRow key={entry.id} entry={entry} fresh={fresh.has(entry.id)} />
            ))}
          </div>
        </div>
      ) : null}
      {entries.isPending ? (
        <Skeleton className="h-24 rounded-xl" />
      ) : shown.length ? (
        KINDS.map((kind) => {
          const ofKind = shown.filter((entry) => entry.kind === kind);
          return ofKind.length ? (
            <div key={kind}>
              <h4 className="border-border border-b pb-2 text-sm font-medium">{words.sections[kind]}</h4>
              <div className="divide-border divide-y">
                {ofKind.map((entry) => (
                  <MemoryRow key={entry.id} entry={entry} fresh={fresh.has(entry.id)} />
                ))}
              </div>
            </div>
          ) : null;
        })
      ) : drafts.length ? null : (
        <p className="text-muted-foreground py-1 text-sm">{retired ? words.noneRetired : words.none}</p>
      )}
      {/* Kept in sight at the foot of the part as the list scrolls, as the chat's box is; hidden,
          never unmounted, while the retired are shown, so what was typed stays. */}
      {asks ? (
        <div hidden={retired} className="bg-popover sticky -bottom-5 -mb-5 pt-1 pb-5">
          <MemoryBox onDone={(done) => setFresh(new Set(done.changes.map((each) => each.entry.id)))} />
        </div>
      ) : null}
    </section>
  );
}

function MemoryBox({ onDone }: { onDone: (changes: Changes) => void }) {
  const { t } = useI18n();
  const [text, setText] = useState("");
  const [last, setLast] = useState<Changes | null>(null);
  // Each answer opens closed, its reason behind See why.
  const [answers, setAnswers] = useState(0);
  const tell = useTell((done) => {
    setLast(done);
    setAnswers((count) => count + 1);
    // What changed something is sent; what changed nothing stays, to be put another way.
    if (done.changes.length) setText("");
    onDone(done);
  });
  const failed = useWhy(tell.error);
  const told = text.trim();
  return (
    <form
      className="grid gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (told && !tell.isPending) {
          setLast(null);
          tell.mutate({ text: told });
        }
      }}
    >
      <Status
        key={answers}
        pending={tell.isPending}
        changes={last?.changes ?? null}
        why={last?.reason ?? ""}
        error={tell.isError ? failed : ""}
      />
      <Composer value={text} onChange={setText} placeholder={t.memory.tell} busy={tell.isPending} />
    </form>
  );
}

/** The entry a chat's `remember` drafted, read by the id its result names: the card under
 * Tamga's answer, with the owner's Confirm. */
export function DraftedEntry({ result }: { result: unknown }) {
  const { t } = useI18n();
  const id = draftedId(result);
  const read = useQuery({
    queryKey: ["memory", "entry", id],
    queryFn: () => getMemoryEntry(id ?? ""),
    enabled: id !== null,
    retry: false,
  });
  if (id === null) return null;
  return (
    <div data-slot="chat-card" className="border-border bg-card my-3 rounded-2xl border px-4 py-1">
      {read.data ? (
        <MemoryRow entry={read.data} />
      ) : read.isError ? (
        <p className="text-muted-foreground py-2.5 text-sm">{t.assistant.cards.gone}</p>
      ) : (
        <Skeleton className="my-2.5 h-10 rounded-xl" />
      )}
    </div>
  );
}

/** The entry's id in what `remember` returned: an object, or the same as JSON; nothing when the
 * tool refused. */
function draftedId(result: unknown): string | null {
  let value = result;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      return null;
    }
  }
  if (value && typeof value === "object" && "entry_id" in value && typeof value.entry_id === "string") {
    return value.entry_id;
  }
  return null;
}
