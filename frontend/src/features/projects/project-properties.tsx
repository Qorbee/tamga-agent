"use client";

import {
  Building03Icon,
  Calendar03Icon,
  Cancel01Icon,
  Flag03Icon,
  HashtagIcon,
  Link01Icon,
  PackageIcon,
  PackageReceiveIcon,
  Route01Icon,
  Tick02Icon,
  TruckIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useDeferredValue, useState, type ReactNode } from "react";

import { CanvasLink } from "@/components/canvas-link";
import { Hint } from "@/components/hint";
import { IconButton } from "@/components/icon-button";
import { InlineDateRange } from "@/components/inline-date-range";
import { InlineText } from "@/components/inline-text";
import { T } from "@/components/translated";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import {
  addProjectParty,
  answerProjectParty,
  ApiError,
  listParties,
  listProjectParties,
  removeProjectParty,
  updateProject,
  type Party,
  type Project,
  type ProjectParty,
  type ProjectUpdate,
} from "@/lib/api";
import type { CanvasView } from "@/lib/canvas-view";
import { useI18n } from "@/lib/i18n/client";
import { useCanEditProjects, useIsOwner } from "@/lib/use-role";
import { cn } from "@/lib/utils";

import { StatusMenu } from "./project-actions";

export const partiesKey = (id: string) => ["project-parties", id] as const;

/** Saving the owner's own value for a project's fields, and the error the server gave a field. */
export function useProjectUpdate(project: Project) {
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: (body: ProjectUpdate) => updateProject(project.id, body),
    onSuccess: (saved) => {
      queryClient.setQueryData(["project", project.id], saved);
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      void queryClient.invalidateQueries({ queryKey: ["field-proposals"] });
    },
  });
  const { t } = useI18n();
  // A failed save is shown on the field that was saved, since its editor has already closed: the
  // server's words when it named that field, else a general one.
  const fieldError = (...fields: string[]) => {
    if (!save.isError || !fields.some((field) => field in (save.variables ?? {}))) return undefined;
    const error = save.error instanceof ApiError ? save.error : null;
    return error?.field && fields.includes(error.field) ? error.message : t.errors.generic;
  };
  return { save, fieldError };
}

/**
 * What the project is, one property a row, each edited where it is shown, as in a document's
 * properties: where it stands, who gave the job and who carries it, the route and the load, its
 * days and its number. A firm the owner chose stands over what was read in its mail.
 */
export function ProjectProperties({ project }: { project: Project }) {
  const { t } = useI18n();
  const canEdit = useCanEditProjects();
  const { save, fieldError } = useProjectUpdate(project);
  const parties = useQuery({ queryKey: partiesKey(project.id), queryFn: () => listProjectParties(project.id) });
  const job = project.job;
  const route = [job?.origin, job?.destination].filter(Boolean).join(" → ");
  const edited = (field: string) => project.field_sources[field] === "owner";
  const held = (role: ProjectParty["role"], state: ProjectParty["state"]) =>
    (parties.data ?? []).filter((party) => party.role === role && party.state === state);
  // Until the owner's own choices are read, what was read in mail is not shown in their place.
  const partyField = (role: "client" | "subcontractor", read: string[]) =>
    parties.isPending ? (
      <Skeleton className="h-4 w-32" />
    ) : parties.isError ? (
      <span className="text-danger-foreground text-sm" role="alert">
        {t.errors.load}
      </span>
    ) : (
      <PartyField
        project={project}
        role={role}
        chosen={held(role, "confirmed")}
        suggested={held(role, "suggested")}
        read={read}
        notInBooks={job?.not_in_books ?? []}
        canEdit={canEdit}
        several={role === "subcontractor"}
      />
    );

  return (
    <dl className="grid grid-cols-[minmax(0,8.5rem)_minmax(0,1fr)] items-start gap-x-3 gap-y-1.5 text-sm">
      <Row icon={Flag03Icon} label={t.board.status}>
        <StatusMenu project={project} />
      </Row>
      <Row icon={Building03Icon} label={t.project.client}>
        {partyField("client", job?.client ? [job.client] : [])}
      </Row>
      <Row icon={TruckIcon} label={t.project.subcontractor}>
        {partyField("subcontractor", job?.subcontractors ?? [])}
      </Row>
      {route ? (
        <Row icon={Route01Icon} label={t.project.route}>
          <T>{route}</T>
        </Row>
      ) : null}
      {job?.consignee ? (
        <Row icon={PackageReceiveIcon} label={t.project.consignee}>
          {job.consignee}
        </Row>
      ) : null}
      {job?.cargo || job?.trucks ? (
        <Row icon={PackageIcon} label={t.project.cargo}>
          {job.cargo ? <T>{job.cargo}</T> : null}
          {job.cargo && job.trucks ? " · " : null}
          {job.trucks ? t.board.trucks(job.trucks) : null}
        </Row>
      ) : null}
      <Row icon={Calendar03Icon} label={t.project.daysShort}>
        <InlineDateRange
          value={{ start: project.starts_on, end: project.ends_on }}
          labels={{ start: t.project.startsOn, end: t.project.endsOn, edit: t.project.editDays }}
          empty={t.project.empty}
          pending={save.isPending}
          error={fieldError("starts_on", "ends_on")}
          onSave={
            canEdit
              ? (changed) =>
                  save.mutate({
                    ...("start" in changed ? { starts_on: changed.start } : {}),
                    ...("end" in changed ? { ends_on: changed.end } : {}),
                  })
              : undefined
          }
        />
        {edited("starts_on") || edited("ends_on") ? <Edited /> : null}
      </Row>
      <Row icon={HashtagIcon} label={t.board.number}>
        <InlineText
          value={project.number}
          label={t.project.editNumber}
          maxLength={60}
          className="font-mono"
          pending={save.isPending}
          error={fieldError("number")}
          onSave={canEdit ? (number) => save.mutate({ number }) : undefined}
        />
        {edited("number") ? <Edited /> : null}
        {project.aliases.length > 0 ? (
          <span className="text-muted-foreground font-mono text-xs">{t.board.aliases(project.aliases.join(", "))}</span>
        ) : null}
      </Row>
    </dl>
  );
}

function Row({ icon, label, children }: { icon: IconSvgElement; label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground flex min-h-7 items-center gap-2">
        <HugeiconsIcon icon={icon} strokeWidth={1.75} className="size-4 shrink-0" aria-hidden />
        <span className="truncate">{label}</span>
      </dt>
      <dd className="flex min-h-7 min-w-0 flex-wrap items-center gap-x-2 gap-y-1">{children}</dd>
    </>
  );
}

/** A quiet mark on a value the owner set: later mail proposes a change instead of making it. */
function Edited() {
  const { t } = useI18n();
  return <span className="text-muted-foreground text-xs">{t.board.ownerEdited}</span>;
}

/**
 * The firm in a role on the project: the one the owner chose, or what was read in its mail until
 * then. A tap opens a search over the company's parties, those from the books first; the client
 * is one firm, subcontractors may be several.
 */
/** Whether two kinds of signal name a suggested firm: it stands for the role until the owner says. */
function agreed(party: ProjectParty): boolean {
  return new Set(party.signals).size >= 2;
}

/** Firms one to a line, so a job's several subcontractors read as so many; a note after the last. */
function Names({ names, note }: { names: (string | null | undefined)[]; note?: string }) {
  const shown = names.filter((name): name is string => Boolean(name));
  return (
    <span className="grid gap-0.5 [overflow-wrap:anywhere]">
      {shown.map((name, at) => (
        <span key={`${name}-${at}`}>
          {name}
          {note && at === shown.length - 1 ? <span className="text-muted-foreground text-xs"> · {note}</span> : null}
        </span>
      ))}
    </span>
  );
}

/** Why Tamga names a firm, as a link's icon: its hint says what named it, a tap opens the first
 * paper that did, and a count tells how many more. */
function FirmProof({ why, sources }: { why: string; sources: ProjectParty["evidence"] }) {
  const { t } = useI18n();
  const opened = sources.filter((item) => SOURCES[item.item_kind]);
  const [first, ...rest] = opened;
  const label = first ? `${why} · ${t.project.openSource[first.item_kind]}` : why;
  const mark = (
    <>
      <HugeiconsIcon icon={Link01Icon} strokeWidth={1.75} className="size-3.5" aria-hidden />
      {rest.length > 0 ? <span className="text-[0.7rem] tabular-nums">+{rest.length}</span> : null}
    </>
  );
  const look = "text-muted-foreground inline-flex h-6 min-w-6 shrink-0 items-center justify-center gap-0.5 rounded-full px-1";
  const view = first ? SOURCES[first.item_kind] : undefined;
  return (
    <Hint label={label}>
      {first && view ? (
        <CanvasLink view={{ kind: view, id: first.item_id }} aria-label={label} className={cn(look, "hover:text-foreground hover:bg-accent/60")}>
          {mark}
        </CanvasLink>
      ) : (
        <span role="img" aria-label={label} className={look}>
          {mark}
        </span>
      )}
    </Hint>
  );
}

function PartyField({
  project,
  role,
  chosen,
  suggested,
  read,
  notInBooks,
  canEdit,
  several = false,
}: {
  project: Project;
  role: "client" | "subcontractor";
  chosen: ProjectParty[];
  // Firms Tamga suggests in the role, from what names them.
  suggested: ProjectParty[];
  read: string[];
  // Of the names read, those no card in the books holds.
  notInBooks: string[];
  canEdit: boolean;
  several?: boolean;
}) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [words, setWords] = useState("");
  const q = useDeferredValue(words.trim());
  const found = useQuery({
    queryKey: ["parties", { q, kind: "organisation" }],
    queryFn: () => listParties({ q: q || undefined, kind: "organisation" }),
    enabled: open,
  });
  const done = () => void queryClient.invalidateQueries({ queryKey: partiesKey(project.id) });
  const choose = useMutation({
    mutationFn: async (party: Party) => {
      await addProjectParty({ project_id: project.id, party_id: party.id, role, layer: role === "client" ? 0 : 1 });
      // The client is one firm: the one chosen before gives way, only once the new one is stored,
      // so a failed save never leaves the project with none.
      if (!several) for (const each of chosen) await removeProjectParty(each.id);
    },
    // The list is read again either way: a new client may be stored though the old one stayed.
    onSettled: done,
    onSuccess: () => {
      if (!several) setOpen(false);
    },
  });
  const remove = useMutation({ mutationFn: (id: string) => removeProjectParty(id), onSuccess: done });
  const chosenIds = new Set(chosen.map((each) => each.party_id));
  // A client the owner named leaves nothing to ask; a job can have more subcontractors.
  const unanswered = several || chosen.length === 0 ? suggested : [];
  const standing = chosen.length === 0 ? unanswered.filter(agreed) : [];
  const shown =
    chosen.length > 0 ? (
      <Names names={chosen.map((each) => each.party_name)} />
    ) : standing.length > 0 ? (
      <Names names={standing.map((each) => each.party_name)} note={t.project.suggestedFirm} />
    ) : read.length > 0 ? (
      <Names
        names={read}
        note={read.every((name) => notInBooks.includes(name)) ? t.project.notInBooks : t.project.readFromMail}
      />
    ) : (
      <span className="text-muted-foreground">{t.project.empty}</span>
    );

  const asked =
    unanswered.length > 0 ? (
      <SuggestedFirms
        project={project}
        role={role}
        firms={unanswered}
        mailNames={chosen.length === 0 ? read.filter((name) => notInBooks.includes(name)) : []}
      />
    ) : null;
  if (!canEdit)
    return (
      <span className="grid gap-1">
        {shown}
        {asked}
      </span>
    );
  return (
    <span className="grid gap-1">
      <Popover
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) setWords("");
        }}
      >
        <PopoverTrigger asChild>
          <button
            type="button"
            aria-label={t.project.chooseParty(role === "client" ? t.project.client : t.project.subcontractor)}
            className="hover:bg-accent/60 focus-visible:ring-ring/50 -mx-1.5 min-w-0 rounded-lg px-1.5 text-start outline-none focus-visible:ring-2"
          >
            {shown}
          </button>
        </PopoverTrigger>
        <PopoverContent className="grid gap-2 p-2">
          {chosen.length > 0 ? (
            <ul className="grid gap-0.5">
              {chosen.map((each) => (
                <li key={each.id} className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm">
                  <HugeiconsIcon icon={Tick02Icon} strokeWidth={1.75} className="size-4 shrink-0" aria-hidden />
                  <span className="min-w-0 flex-1 truncate">{each.party_name}</span>
                  <button
                    type="button"
                    className="text-muted-foreground hover:text-foreground rounded p-0.5"
                    disabled={remove.isPending}
                    onClick={() => remove.mutate(each.id)}
                  >
                    <HugeiconsIcon icon={Cancel01Icon} strokeWidth={1.75} className="size-4" />
                    <span className="sr-only">{t.project.removeParty(each.party_name)}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
          <Input
            autoFocus
            value={words}
            placeholder={t.project.searchParties}
            aria-label={t.project.searchParties}
            onChange={(event) => setWords(event.target.value)}
          />
          <ul className="grid max-h-60 gap-0.5 overflow-y-auto">
            {(found.data?.items ?? [])
              .filter((party) => !chosenIds.has(party.id))
              .map((party) => (
                <li key={party.id}>
                  <button
                    type="button"
                    disabled={choose.isPending}
                    onClick={() => choose.mutate(party)}
                    className={cn(
                      "hover:bg-accent focus-visible:bg-accent w-full truncate rounded-lg px-2 py-1.5 text-start text-sm outline-none",
                    )}
                  >
                    {party.name}
                  </button>
                </li>
              ))}
            {found.data && found.data.items.length === 0 ? (
              <li className="text-muted-foreground px-2 py-1.5 text-sm">{q ? t.project.noParties : t.project.noFirms}</li>
            ) : null}
          </ul>
          {choose.isError || remove.isError ? (
            <p className="text-danger-foreground px-2 text-xs" role="alert">
              {t.errors.generic}
            </p>
          ) : null}
        </PopoverContent>
      </Popover>
      {asked}
    </span>
  );
}

/**
 * The firms Tamga suggests in a role, each with what names it and a tap to the mail or invoice
 * it was read from: two kinds of signal agreeing are said as such, one alone is asked. The owner
 * answers yes or no; a no keeps the firm from being suggested there again.
 */
/**
 * Firms Tamga suggests in a role, each to confirm or refuse. Where the mail names a firm in that
 * role that is not in the books (``mailNames``), a firm only one kind of signal names is asked
 * against it: the books' firm may be another, or the same under its legal name.
 */
function SuggestedFirms({
  project,
  role,
  firms,
  mailNames,
}: {
  project: Project;
  role: "client" | "subcontractor";
  firms: ProjectParty[];
  mailNames: string[];
}) {
  const { t } = useI18n();
  const isOwner = useIsOwner();
  const queryClient = useQueryClient();
  const answer = useMutation({
    mutationFn: ({ id, state }: { id: string; state: "confirmed" | "rejected" }) => answerProjectParty(id, { state }),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: partiesKey(project.id) });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });
  const label = role === "client" ? t.project.client : t.project.subcontractor;
  return (
    <ul className="grid gap-1">
      {firms.map((firm) => {
        // A tap to each kind of item that named it: its first mail, its first invoice.
        const sources = firm.evidence.filter(
          (item, at) => firm.evidence.findIndex((other) => other.item_kind === item.item_kind) === at,
        );
        return (
          <li key={firm.id} className="bg-secondary/60 grid gap-0.5 rounded-lg px-2 py-1.5 text-xs">
            <span className="flex items-start gap-1.5">
              <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">
                {agreed(firm)
                  ? firm.party_name
                  : mailNames.length > 0
                    ? t.project.askAgainstMail(firm.party_name, label, mailNames.join(", "))
                    : t.project.askFirm(firm.party_name, label)}
              </span>
              <FirmProof
                why={firm.signals.map((signal) => t.project.firmSignals[signal] ?? signal).join(" · ")}
                sources={sources}
              />
              {isOwner ? (
                <span className="flex shrink-0 items-center gap-0.5">
                  <IconButton
                    icon={Tick02Icon}
                    label={t.project.confirmFirm(firm.party_name)}
                    size="icon-xs"
                    disabled={answer.isPending}
                    onClick={() => answer.mutate({ id: firm.id, state: "confirmed" })}
                  />
                  <IconButton
                    icon={Cancel01Icon}
                    label={t.project.rejectFirm(firm.party_name)}
                    size="icon-xs"
                    className="text-muted-foreground"
                    disabled={answer.isPending}
                    onClick={() => answer.mutate({ id: firm.id, state: "rejected" })}
                  />
                </span>
              ) : null}
            </span>
            {answer.isError ? (
              <span className="text-danger-foreground" role="alert">
                {t.errors.generic}
              </span>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

// The record an item a firm was read from opens as.
const SOURCES: Partial<Record<ProjectParty["evidence"][number]["item_kind"], CanvasView["kind"]>> = {
  message: "mail",
  invoice: "invoice",
};
