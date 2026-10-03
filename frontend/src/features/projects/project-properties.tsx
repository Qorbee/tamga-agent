"use client";

import {
  Building03Icon,
  Calendar03Icon,
  Cancel01Icon,
  Flag03Icon,
  HashtagIcon,
  PackageIcon,
  PackageReceiveIcon,
  Route01Icon,
  Tick02Icon,
  TruckIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useDeferredValue, useState, type ReactNode } from "react";

import { InlineDateRange } from "@/components/inline-date-range";
import { InlineText } from "@/components/inline-text";
import { T } from "@/components/translated";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import {
  addProjectParty,
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
import { useI18n } from "@/lib/i18n/client";
import { useCanEditProjects } from "@/lib/use-role";
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
  const chosen = (role: ProjectParty["role"]) => (parties.data ?? []).filter((party) => party.role === role);
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
        chosen={chosen(role)}
        read={read}
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
function PartyField({
  project,
  role,
  chosen,
  read,
  canEdit,
  several = false,
}: {
  project: Project;
  role: "client" | "subcontractor";
  chosen: ProjectParty[];
  read: string[];
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
  const shown =
    chosen.length > 0 ? (
      <span className="[overflow-wrap:anywhere]">{chosen.map((each) => each.party_name).join(", ")}</span>
    ) : read.length > 0 ? (
      <span className="[overflow-wrap:anywhere]">
        {read.join(", ")} <span className="text-muted-foreground text-xs">· {t.project.readFromMail}</span>
      </span>
    ) : (
      <span className="text-muted-foreground">{t.project.empty}</span>
    );

  if (!canEdit) return shown;
  return (
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
  );
}
