"use client";

import {
  Alert02Icon,
  Cancel01Icon,
  CheckmarkBadge01Icon,
  MailAccount01Icon,
  SentIcon,
  Undo02Icon,
  UserIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { IconButton } from "@/components/icon-button";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  addCompanyDomain,
  getCompanyProfile,
  removeCompanyDomain,
  setCompanyTaxId,
  type CheckFinding,
  type CompanyProfile,
  type IdentityChange,
  type IdentityCheck,
  type OwnDomain,
} from "@/lib/api";
import type { Dictionary } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

const PROFILE = ["company-profile"] as const;

type Tone = "good" | "doubt" | "plain";
const PILL: Record<Tone, string> = {
  good: "bg-success-soft text-success-foreground",
  doubt: "bg-warning-soft text-warning-foreground",
  plain: "bg-muted text-muted-foreground",
};
const SOURCE_ICONS: Record<OwnDomain["sources"][number], IconSvgElement> = {
  mailbox: MailAccount01Icon,
  sent_mail: SentIcon,
  registry: CheckmarkBadge01Icon,
  owner: UserIcon,
};
const REGISTRY_TONE: Record<NonNullable<CompanyProfile["tax_id"]>["registry"], Tone> = {
  found: "good",
  not_found: "plain",
  not_asked: "doubt",
};

/**
 * Who the company is, in Settings: its legal name and tax number, and the mail domains it writes
 * from, each with what shows it. Only the owner changes them; every change is checked first, and
 * what the check doubts is shown before anything is saved.
 */
export function CompanyIdentity({ owner }: { owner: boolean }) {
  const { t } = useI18n();
  const words = t.settings.identity;
  const profile = useQuery({ queryKey: PROFILE, queryFn: getCompanyProfile });

  return (
    <section className="flex flex-col gap-3">
      <div className="grid gap-0.5">
        <h3 className="text-muted-foreground text-xs font-medium">{words.title}</h3>
        <p className="text-muted-foreground text-xs text-pretty">{words.hint}</p>
      </div>
      {profile.isError ? (
        <p className="text-danger-foreground text-sm" role="alert">
          {t.errors.load}
        </p>
      ) : profile.data ? (
        <>
          <TaxNumber profile={profile.data} owner={owner} />
          <Domains profile={profile.data} owner={owner} />
        </>
      ) : (
        <Skeleton className="h-28 rounded-2xl" />
      )}
    </section>
  );
}

function Pill({ tone, icon, label }: { tone: Tone; icon?: IconSvgElement; label: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        PILL[tone],
      )}
    >
      {icon ? <HugeiconsIcon icon={icon} strokeWidth={2} className="size-3.5" aria-hidden /> : null}
      {label}
    </span>
  );
}

/** Saves through `send`; a check with doubts waits for the owner's confirmation. */
function useChecked<T>(
  send: (body: T, confirm: boolean) => Promise<IdentityChange>,
  onSaved: () => void,
) {
  const queryClient = useQueryClient();
  const [asked, setAsked] = useState<{ body: T; check: IdentityCheck } | null>(null);
  const change = useMutation({
    mutationFn: ({ body, confirm }: { body: T; confirm: boolean }) => send(body, confirm),
    onSuccess: (answer, { body }) => {
      queryClient.setQueryData(PROFILE, answer.profile);
      setAsked(answer.saved ? null : { body, check: answer.check });
      if (answer.saved) onSaved();
    },
  });
  return {
    check: asked?.check ?? null,
    pending: change.isPending,
    failed: change.isError,
    submit: (body: T) => change.mutate({ body, confirm: false }),
    confirm: () => (asked ? change.mutate({ body: asked.body, confirm: true }) : undefined),
    reset: () => {
      setAsked(null);
      change.reset();
    },
  };
}

function TaxNumber({ profile, owner }: { profile: CompanyProfile; owner: boolean }) {
  const { t } = useI18n();
  const words = t.settings.identity;
  const [editing, setEditing] = useState(false);
  const [taxId, setTaxId] = useState("");
  const [legalName, setLegalName] = useState("");
  const close = () => {
    setEditing(false);
    setTaxId("");
    setLegalName("");
  };
  const saving = useChecked(
    (body: { tax_id: string; legal_name: string | null }, confirm) => setCompanyTaxId({ ...body, confirm }),
    close,
  );
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (taxId.trim()) saving.submit({ tax_id: taxId, legal_name: legalName.trim() });
  };
  const own = profile.tax_id;
  const proposal = profile.proposal;

  if (own && !editing) {
    return (
      <div className="border-border bg-card grid gap-2 rounded-2xl border p-3">
        <div className="grid gap-0.5">
          <span className="text-muted-foreground text-xs">{words.taxId}</span>
          <span className="font-medium text-pretty break-words">{profile.legal_name ?? profile.name}</span>
          <span className="text-sm tabular-nums">{own.tax_id}</span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Pill
            tone={REGISTRY_TONE[own.registry]}
            icon={own.registry === "found" ? CheckmarkBadge01Icon : undefined}
            label={words.registry[own.registry]}
          />
          <Evidence papers={own.evidence.papers} sent={own.evidence.sent_mails} />
        </div>
        {owner ? (
          <Button
            variant="ghost"
            size="sm"
            className="justify-self-start"
            onClick={() => {
              // The number as it is, so adding the legal name needs no retyping.
              setTaxId(own.tax_id);
              setLegalName(profile.legal_name ?? "");
              setEditing(true);
            }}
          >
            {words.change}
          </Button>
        ) : null}
      </div>
    );
  }

  if (proposal && !own && !editing) {
    return (
      <div className="bg-warning-soft grid gap-2 rounded-2xl p-3">
        <p className="font-medium text-pretty tabular-nums">{words.proposal(proposal.tax_id)}</p>
        <p className="text-muted-foreground text-xs text-pretty">{words.proposalWhy}</p>
        <Evidence papers={proposal.evidence.papers} sent={proposal.evidence.sent_mails} />
        <Findings check={saving.check} kind="tax" />
        {owner ? (
          <div className="flex flex-wrap gap-2">
            {saving.check?.verdict === "confirm" ? (
              <Button size="sm" variant="outline" disabled={saving.pending} onClick={saving.confirm}>
                {words.confirmTaxId}
              </Button>
            ) : (
              <Button
                size="sm"
                disabled={saving.pending}
                onClick={() => saving.submit({ tax_id: proposal.tax_id, legal_name: null })}
              >
                {words.proposalYes}
              </Button>
            )}
            <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
              {words.proposalNo}
            </Button>
          </div>
        ) : null}
        <Failed shown={saving.failed} />
      </div>
    );
  }

  if (!owner) {
    return (
      <div className="border-border bg-card grid gap-0.5 rounded-2xl border p-3">
        <span className="text-muted-foreground text-xs">{words.taxId}</span>
        <span className="text-muted-foreground text-sm">{words.noTaxId}</span>
      </div>
    );
  }

  return (
    <form className="border-border bg-card grid gap-2 rounded-2xl border p-3" onSubmit={submit}>
      <label className="grid gap-1">
        <span className="text-muted-foreground text-xs">{words.taxId}</span>
        <Input
          value={taxId}
          disabled={saving.pending}
          inputMode="numeric"
          autoComplete="off"
          placeholder={words.taxIdPlaceholder}
          onChange={(event) => {
            setTaxId(event.target.value);
            saving.reset();
          }}
        />
      </label>
      <label className="grid gap-1">
        <span className="text-muted-foreground text-xs">{words.legalName}</span>
        <Input
          value={legalName}
          disabled={saving.pending}
          autoComplete="organization"
          onChange={(event) => {
            // A doubt was about what was sent; confirming must send what is on screen.
            setLegalName(event.target.value);
            saving.reset();
          }}
        />
      </label>
      <Findings check={saving.check} kind="tax" />
      <div className="flex flex-wrap gap-2">
        {saving.check?.verdict === "confirm" ? (
          <Button type="button" size="sm" variant="outline" disabled={saving.pending} onClick={saving.confirm}>
            {words.confirmTaxId}
          </Button>
        ) : (
          <Button type="submit" size="sm" disabled={saving.pending || !taxId.trim()}>
            {words.save}
          </Button>
        )}
        {own || proposal ? (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={saving.pending}
            onClick={() => {
              close();
              saving.reset();
            }}
          >
            {words.cancel}
          </Button>
        ) : null}
      </div>
      <Failed shown={saving.failed} />
    </form>
  );
}

function Evidence({ papers, sent }: { papers: number; sent: number }) {
  const { t } = useI18n();
  const words = t.settings.identity;
  const seen = [sent ? words.sentMails(sent) : null, papers ? words.papers(papers) : null].filter(Boolean);
  if (seen.length === 0) return null;
  return <span className="text-muted-foreground text-xs">{seen.join(" · ")}</span>;
}

function Domains({ profile, owner }: { profile: CompanyProfile; owner: boolean }) {
  const { t } = useI18n();
  const words = t.settings.identity;
  const queryClient = useQueryClient();
  const [domain, setDomain] = useState("");
  const adding = useChecked(
    (body: { domain: string }, confirm) => addCompanyDomain({ ...body, confirm }),
    () => setDomain(""),
  );
  const removing = useMutation({
    mutationFn: removeCompanyDomain,
    onSuccess: (updated) => queryClient.setQueryData(PROFILE, updated),
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (domain.trim()) adding.submit({ domain });
  };

  return (
    <div className="grid gap-2">
      <div className="grid gap-0.5">
        <span className="text-muted-foreground text-xs">{words.domains}</span>
        <span className="text-muted-foreground text-xs text-pretty">{words.domainsHint}</span>
      </div>
      {profile.domains.length > 0 ? (
        <ul className="divide-border border-border bg-card divide-y rounded-2xl border">
          {profile.domains.map((own) => (
            <DomainRow
              key={own.domain}
              own={own}
              owner={owner}
              busy={removing.isPending || adding.pending}
              onRemove={() => removing.mutate(own.domain)}
              onRestore={() => adding.submit({ domain: own.domain })}
            />
          ))}
        </ul>
      ) : null}
      {owner ? (
        <form className="grid gap-2" onSubmit={submit}>
          <div className="flex flex-wrap gap-2">
            <Input
              className="min-w-0 flex-1 basis-48"
              value={domain}
              disabled={adding.pending}
              autoComplete="off"
              inputMode="url"
              aria-label={words.domains}
              placeholder={words.domainPlaceholder}
              onChange={(event) => {
                setDomain(event.target.value);
                adding.reset();
              }}
            />
            {adding.check?.verdict === "confirm" ? (
              <Button type="button" variant="outline" disabled={adding.pending} onClick={adding.confirm}>
                {words.confirmDomain}
              </Button>
            ) : (
              <Button type="submit" variant="outline" disabled={adding.pending || !domain.trim()}>
                {words.add}
              </Button>
            )}
          </div>
          <Findings
            check={adding.check}
            kind="domain"
            onSuggestion={(suggestion) => {
              setDomain(suggestion);
              adding.reset();
            }}
          />
          <Failed shown={adding.failed || removing.isError} />
        </form>
      ) : null}
    </div>
  );
}

function DomainRow({
  own,
  owner,
  busy,
  onRemove,
  onRestore,
}: {
  own: OwnDomain;
  owner: boolean;
  busy: boolean;
  onRemove: () => void;
  onRestore: () => void;
}) {
  const { t } = useI18n();
  const words = t.settings.identity;
  return (
    <li className="flex items-center gap-2 py-2 ps-3 pe-2">
      <span className="grid min-w-0 flex-1 gap-1">
        <span className={cn("font-medium break-all", !own.ours && "text-muted-foreground")}>{own.domain}</span>
        <span className="flex flex-wrap gap-1.5">
          {own.ours ? (
            own.sources.map((source) => (
              <Pill
                key={source}
                tone={source === "owner" ? "plain" : "good"}
                icon={SOURCE_ICONS[source]}
                label={source === "sent_mail" ? words.sources.sent_mail(own.sent) : words.sources[source]}
              />
            ))
          ) : (
            <Pill tone="plain" label={words.notOurs} />
          )}
        </span>
      </span>
      {owner && own.removable ? (
        own.ours ? (
          <IconButton icon={Cancel01Icon} label={words.markNotOurs(own.domain)} disabled={busy} onClick={onRemove} />
        ) : (
          <IconButton icon={Undo02Icon} label={words.markOurs} disabled={busy} onClick={onRestore} />
        )
      ) : null}
    </li>
  );
}

/** What a check found against a value, as the owner reads it; a doubt in ochre, a refusal in
 * terracotta. */
function Findings({
  check,
  kind,
  onSuggestion,
}: {
  check: IdentityCheck | null;
  kind: "tax" | "domain";
  onSuggestion?: (suggestion: string) => void;
}) {
  const { t } = useI18n();
  if (!check || check.verdict === "ok" || check.findings.length === 0) return null;
  const doubt = check.verdict === "confirm";
  return (
    <ul className={cn("grid gap-1 text-xs", doubt ? "text-warning-foreground" : "text-danger-foreground")} role="status">
      {check.findings.map((finding, index) => (
        <li key={index} className="flex items-start gap-1.5 text-pretty">
          <HugeiconsIcon icon={Alert02Icon} strokeWidth={2} className="mt-px size-3.5 shrink-0" aria-hidden />
          {finding.reason === "spelling" && finding.suggestion && onSuggestion ? (
            <Button
              type="button"
              variant="link"
              size="sm"
              className="h-auto p-0 text-xs"
              onClick={() => onSuggestion(finding.suggestion ?? "")}
            >
              {said(finding, kind, t.settings.identity.findings)}
            </Button>
          ) : (
            <span>{said(finding, kind, t.settings.identity.findings)}</span>
          )}
        </li>
      ))}
    </ul>
  );
}

type FindingWords = Dictionary["settings"]["identity"]["findings"];

function said(finding: CheckFinding, kind: "tax" | "domain", words: FindingWords): string {
  const firm = finding.firm ?? "";
  switch (finding.reason) {
    case "not_valid":
      return words.not_valid[kind];
    case "shared_domain":
      return words.shared_domain;
    case "firms_address":
      return words.firms_address(firm);
    case "firms_tax_id":
      return words.firms_tax_id(firm);
    case "firms_name":
      return words.firms_name(firm);
    case "only_received":
      return words.only_received(finding.count ?? 0);
    case "no_mail":
      return words.no_mail;
    case "spelling":
      return words.spelling(finding.suggestion ?? "");
    case "registry_name":
      return words.registry_name(finding.title ?? "");
    case "registry_domain":
      return words.registry_domain(finding.domain ?? "");
    case "not_seen":
      return words.not_seen;
  }
}

function Failed({ shown }: { shown: boolean }) {
  const { t } = useI18n();
  if (!shown) return null;
  return (
    <span className="text-danger-foreground text-xs" role="alert">
      {t.errors.generic}
    </span>
  );
}
