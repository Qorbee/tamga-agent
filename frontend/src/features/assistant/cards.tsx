"use client";

import { makeAssistantDataUI, makeAssistantToolUI, type AssistantDataUI, type DataMessagePartProps } from "@assistant-ui/react";
import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { DecisionRow } from "@/components/decision-card";
import { DraftedEntry } from "@/components/memory";
import { Skeleton } from "@/components/ui/skeleton";
import { ProjectCard, Questions } from "@/components/waiting-cards";
import {
  ApiError,
  getDecision,
  getPlacement,
  getProject,
  listAllPlacements,
  listPayments,
  type CardContent,
  type CardKind,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";

/**
 * The cards under what Tamga writes on its own (ADR-0018). The server says only which record a
 * card shows; the card reads that record like any page does, so it is the Inbox's own card, it
 * answers through the same requests, and it shows the record as it is now, not as it was when
 * Tamga wrote.
 */
function ProposalCard({ data }: DataMessagePartProps<CardContent>) {
  const project = useQuery({
    queryKey: ["project", data.id],
    queryFn: () => getProject(data.id),
    retry: false,
  });
  const placements = useQuery({
    queryKey: ["placements", { projects: [data.id] }],
    queryFn: () => listAllPlacements({ project_id: [data.id] }),
  });
  return (
    <Card pending={project.isPending || placements.isPending} error={project.error ?? placements.error}>
      {project.data ? (
        <ProjectCard
          project={project.data}
          waiting={(placements.data ?? []).filter((placement) => placement.conversation)}
        />
      ) : null}
    </Card>
  );
}

function QuestionCard({ data }: DataMessagePartProps<CardContent>) {
  const placement = useQuery({
    queryKey: ["placements", data.id],
    queryFn: () => getPlacement(data.id),
    retry: false,
  });
  return (
    <Card pending={placement.isPending} error={placement.error}>
      {placement.data?.conversation ? <Questions placements={[placement.data]} /> : null}
    </Card>
  );
}

/** A bill that waits for the owner's approval, as its row on the Payments page shows it now. */
function ApprovalCard({ data }: DataMessagePartProps<CardContent>) {
  const decision = useQuery({
    queryKey: ["decision", data.id],
    queryFn: () => getDecision(data.id),
    retry: false,
  });
  const settled = decision.data?.state === "settled";
  const payments = useQuery({
    queryKey: ["payments", { decisions: [data.id] }],
    queryFn: () => listPayments({ decision_id: [data.id] }),
    enabled: settled,
  });
  return (
    <Card pending={decision.isPending} error={decision.error}>
      {decision.data ? (
        <div className="border-border bg-card rounded-2xl border py-1 ps-2 pe-3">
          <DecisionRow
            decision={decision.data}
            payment={payments.data?.items.find((payment) => payment.state === "settled" && !payment.voided_at)}
          />
        </div>
      ) : null}
    </Card>
  );
}

/** A card while its record loads, and what is said when the record cannot be shown. */
function Card({ pending, error, children }: { pending: boolean; error: unknown; children: ReactNode }) {
  const { t } = useI18n();
  return (
    <div data-slot="chat-card" className="my-3">
      {error ? (
        <p className="text-muted-foreground border-border rounded-2xl border px-4 py-3 text-sm" role="status">
          {error instanceof ApiError && error.status === 404 ? t.assistant.cards.gone : t.errors.load}
        </p>
      ) : pending ? (
        <Skeleton className="h-32 rounded-2xl" />
      ) : (
        children
      )}
    </div>
  );
}

/** The chat protocol's activity messages reach the thread as data parts under this prefix. */
const card = (kind: CardKind, render: (props: DataMessagePartProps<CardContent>) => ReactNode): AssistantDataUI =>
  makeAssistantDataUI<CardContent>({ name: `agui-activity/${kind}`, render });

/** A renderer for every kind of card the API names: a new kind fails the build until it has one. */
const CARDS: Record<CardKind, AssistantDataUI> = {
  project_proposal: card("project_proposal", ProposalCard),
  conversation_question: card("conversation_question", QuestionCard),
  decision_approval: card("decision_approval", ApprovalCard),
};

/** Where the chat was compacted: above it Tamga works from notes, and every message is still here. */
function CompactedHere() {
  const { t } = useI18n();
  return (
    <div data-slot="chat-compacted" role="separator" className="text-muted-foreground my-4 flex items-center gap-3 text-xs">
      <span className="bg-border h-px flex-1" />
      <span className="max-w-[75%] text-center text-pretty">{t.assistant.context.compacted}</span>
      <span className="bg-border h-px flex-1" />
    </div>
  );
}

const Compacted = makeAssistantDataUI({ name: "agui-activity/compaction", render: CompactedHere });

/** What the chat's `remember` drafted, as a card the owner confirms under Tamga's answer
 * (ADR-0024); until the tool has answered, nothing. */
const Remembered = makeAssistantToolUI<Record<string, unknown>, unknown>({
  toolName: "remember",
  // Shown on its own, not folded into the run's closed list of steps: the owner confirms here.
  display: "standalone",
  render: ({ result }) => (result === undefined ? null : <DraftedEntry result={result} />),
});

/** Registers the cards and the mark with the chat; rendered once, inside the chat's runtime. */
export function ChatCards() {
  return (
    <>
      {Object.entries(CARDS).map(([kind, Registered]) => (
        <Registered key={kind} />
      ))}
      <Compacted />
      <Remembered />
    </>
  );
}
