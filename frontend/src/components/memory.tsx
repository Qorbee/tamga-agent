"use client";

import { MoreHorizontalIcon, SentIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Hint } from "@/components/hint";
import { IconButton } from "@/components/icon-button";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  ApiError,
  changeMemoryEntry,
  discardMemoryEntry,
  draftMemoryEntries,
  getMemoryEntry,
  getParty,
  getProject,
  listAllMemory,
  writeMemoryEntry,
  type MemoryEntry,
  type MemoryKind,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";
import { useAsks, useIsOwner } from "@/lib/use-role";
import { cn } from "@/lib/utils";

const KINDS: MemoryKind[] = ["term", "alias", "person", "preference", "practice"];
const LONGEST = 500;

/** Everything that shows an entry reads it again after a change. */
function useRefresh() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["memory"] });
  };
}

/**
 * One thing the owner taught Tamga (ADR-0024): its kind, its words and what it is about, and the
 * owner's answers. A draft from a chat reaches no agent until the owner confirms it here; one
 * that holds is corrected or retired, never deleted, so what agents were given stays traceable.
 */
export function MemoryRow({ entry, grouped = false }: { entry: MemoryEntry; grouped?: boolean }) {
  const { t } = useI18n();
  const words = t.memory;
  const owner = useIsOwner();
  const refresh = useRefresh();
  const [editing, setEditing] = useState(false);
  const change = useMutation({
    mutationFn: (body: Parameters<typeof changeMemoryEntry>[1]) => changeMemoryEntry(entry.id, body),
    onSuccess: () => setEditing(false),
    onSettled: refresh,
  });
  const discard = useMutation({ mutationFn: () => discardMemoryEntry(entry.id), onSettled: refresh });
  const busy = change.isPending || discard.isPending;
  const draft = entry.state === "proposed";

  return (
    <div data-slot="memory-entry" className="grid gap-2 py-2.5">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-muted-foreground flex flex-wrap items-center gap-x-2 text-xs">
            <span className="font-medium">{words.kinds[entry.kind]}</span>
            <Subject entry={entry} />
            {/* Among the drafts, their heading already says they wait. */}
            {draft && !grouped ? <span className="text-warning-foreground">{words.waits}</span> : null}
            {entry.state === "retired" ? <span>{words.retired}</span> : null}
          </p>
          {editing ? null : (
            <p className={cn("text-sm text-pretty break-words", entry.state === "retired" && "text-muted-foreground line-through")}>
              {entry.text}
            </p>
          )}
        </div>
        {owner && !editing && !draft ? (
          <DropdownMenu modal={false}>
            <Hint label={words.more}>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon" className="text-muted-foreground -me-2" aria-label={words.more} disabled={busy}>
                  <HugeiconsIcon icon={MoreHorizontalIcon} strokeWidth={1.75} />
                </Button>
              </DropdownMenuTrigger>
            </Hint>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => setEditing(true)}>{words.edit}</DropdownMenuItem>
              {entry.state === "held" ? (
                <DropdownMenuItem onSelect={() => change.mutate({ state: "retired" })}>{words.retire}</DropdownMenuItem>
              ) : (
                <DropdownMenuItem onSelect={() => change.mutate({ state: "held" })}>{words.restore}</DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
      </div>
      {editing ? (
        <EntryForm
          kind={entry.kind}
          text={entry.text}
          busy={busy}
          submit={words.save}
          onCancel={() => setEditing(false)}
          onSubmit={(kind, text) => change.mutate({ kind, text })}
        />
      ) : null}
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
  return about.length ? <span className="min-w-0 truncate">{about.join(" · ")}</span> : null;
}

function EntryForm({
  kind: initialKind,
  text: initialText,
  busy,
  submit,
  onSubmit,
  onCancel,
}: {
  kind: MemoryKind;
  text: string;
  busy: boolean;
  submit: string;
  onSubmit: (kind: MemoryKind, text: string) => void;
  onCancel?: () => void;
}) {
  const { t } = useI18n();
  const words = t.memory;
  const [kind, setKind] = useState<MemoryKind>(initialKind);
  const [text, setText] = useState(initialText);
  const clean = text.trim();
  return (
    <form
      className="grid gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (clean) onSubmit(kind, clean);
      }}
    >
      <Textarea
        value={text}
        maxLength={LONGEST}
        rows={2}
        onChange={(event) => setText(event.target.value)}
        placeholder={words.placeholder}
        aria-label={words.text}
        className="min-h-16 text-sm"
      />
      <div className="flex flex-wrap items-center gap-2">
        <Select value={kind} onValueChange={(value) => setKind(value as MemoryKind)}>
          <SelectTrigger size="sm" aria-label={words.kind} className="min-w-0 flex-1 sm:max-w-48">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {KINDS.map((each) => (
              <SelectItem key={each} value={each}>
                {words.kinds[each]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {onCancel ? (
          <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={onCancel}>
            {words.cancel}
          </Button>
        ) : null}
        <Button type="submit" size="sm" disabled={busy || !clean}>
          {submit}
        </Button>
      </div>
    </form>
  );
}

/**
 * Settings → What Tamga knows: the drafts that wait for the owner, then what holds, and what the
 * owner retired on request. At the bottom, a box to tell Tamga something in one's own words: a
 * model drafts it into entries (`POST /memory/drafts`), each waiting above for the owner's
 * confirmation (ADR-0024). When it cannot draft, the owner writes the entry as it is.
 */
export function MemorySection() {
  const { t } = useI18n();
  const words = t.memory;
  const owner = useIsOwner();
  const asks = useAsks();
  const refresh = useRefresh();
  const [retired, setRetired] = useState(false);
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
    <section className="flex flex-col gap-3">
      <header>
        <h3 className="text-muted-foreground pb-1 text-xs font-medium">{words.title}</h3>
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
              <MemoryRow key={entry.id} entry={entry} grouped />
            ))}
          </div>
        </div>
      ) : null}
      {entries.isPending ? (
        <Skeleton className="h-16 rounded-xl" />
      ) : shown.length ? (
        <div className="divide-border divide-y">
          {shown.map((entry) => (
            <MemoryRow key={entry.id} entry={entry} />
          ))}
        </div>
      ) : drafts.length ? null : (
        <p className="text-muted-foreground py-1 text-sm">{retired ? words.noneRetired : words.none}</p>
      )}
      {/* Hidden, never unmounted, while the retired are shown: what was typed stays. */}
      {asks ? (
        <div hidden={retired}>
          <MemoryComposer owner={owner} onChange={refresh} />
        </div>
      ) : null}
      <Button size="sm" variant="ghost" className="text-muted-foreground self-start" onClick={() => setRetired(!retired)}>
        {retired ? words.showHeld : words.showRetired}
      </Button>
    </section>
  );
}

/** The longest text one drafting reads (`MemoryText`). */
const TOLD = 2000;

function MemoryComposer({ owner, onChange }: { owner: boolean; onChange: () => void }) {
  const { t } = useI18n();
  const words = t.memory;
  const [text, setText] = useState("");
  const [said, setSaid] = useState("");
  // What could not be drafted, for the owner to write as it is.
  const [asIs, setAsIs] = useState<string | null>(null);
  const drafting = useMutation({
    mutationFn: (told: string) => draftMemoryEntries({ text: told }),
    onMutate: () => {
      setSaid("");
      setAsIs(null);
    },
    onSuccess: (drafted, told) => {
      if (drafted.items.length) {
        setText("");
        setSaid(words.drafted(drafted.items.length));
        return;
      }
      // Nothing drafted: the words stay, and the owner may still keep them as they are.
      setSaid(words.nothingToKeep);
      if (owner) setAsIs(told.slice(0, LONGEST));
    },
    onError: (_error, told) => {
      if (owner) setAsIs(told.slice(0, LONGEST));
    },
    onSettled: onChange,
  });
  const write = useMutation({
    mutationFn: writeMemoryEntry,
    onSuccess: () => {
      setAsIs(null);
      setText("");
    },
    onSettled: onChange,
  });
  const told = text.trim();
  const send = () => {
    if (told && !drafting.isPending) drafting.mutate(told);
  };
  const spent = drafting.error instanceof ApiError && drafting.error.status === 429;

  return (
    <div className="grid gap-2">
      <form
        className="border-border bg-card focus-within:border-ring rounded-2xl border p-2 transition-colors"
        onSubmit={(event) => {
          event.preventDefault();
          send();
        }}
      >
        <Textarea
          value={text}
          maxLength={TOLD}
          rows={2}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            // Enter drafts, Shift+Enter starts a new line, as in the chat.
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              send();
            }
          }}
          placeholder={words.tell}
          aria-label={words.tell}
          className="max-h-48 min-h-14 resize-none border-0 bg-transparent px-2 text-sm shadow-none focus-visible:ring-0 dark:bg-transparent"
        />
        <div className="flex items-center justify-between gap-2 ps-2">
          <span className="text-muted-foreground min-w-0 text-xs text-pretty" role="status">
            {drafting.isPending ? words.drafting : said}
          </span>
          <IconButton
            icon={SentIcon}
            label={words.draft}
            type="submit"
            variant="default"
            className="shrink-0 rounded-full"
            disabled={!told || drafting.isPending}
          />
        </div>
      </form>
      {drafting.isError ? (
        <p className="text-danger-foreground text-xs" role="alert">
          {owner ? (spent ? words.spent : words.failed) : t.errors.generic}
        </p>
      ) : null}
      {asIs !== null ? (
        <EntryForm
          kind="practice"
          text={asIs}
          busy={write.isPending}
          submit={words.add}
          onCancel={() => setAsIs(null)}
          onSubmit={(kind, entry) => write.mutate({ kind, text: entry })}
        />
      ) : null}
      {write.isError ? (
        <p className="text-danger-foreground text-xs" role="alert">
          {t.errors.generic}
        </p>
      ) : null}
    </div>
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
