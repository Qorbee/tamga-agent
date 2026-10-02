"use client";

import { useAuiState } from "@assistant-ui/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Hint } from "@/components/hint";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { compactChat, getMe, type Chat } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";
import { chatMessagesKey, useArchivedChats, useChats, useInvalidateChats } from "@/lib/use-chats";
import { cn } from "@/lib/utils";

/** The ring's radius and the length of its whole stroke, in the units of its 20 by 20 box. */
const RADIUS = 8;
const AROUND = 2 * Math.PI * RADIUS;

/**
 * How full the open chat is: what its next question would send Tamga, of what a chat may hold
 * (ADR-0018). A ring beside Send, with the share in figures from half, where compacting is
 * offered and may be left alone: the chat is compacted by itself before it fills. Compacting
 * removes nothing the reader sees.
 */
export function ContextMeter() {
  const id = useAuiState((s) => s.threadListItem.id);
  const open = useChats().data?.items;
  // A chat put away is still opened, from the Archived group, and is as full as it was.
  const archived = useArchivedChats().data?.items;
  const chat = open?.find((item) => item.id === id) ?? archived?.find((item) => item.id === id);
  if (!chat || chat.context_tokens === 0) return null;
  // How a compaction went is one chat's to show: the next chat opened starts without it.
  return <Meter key={chat.id} chat={chat} />;
}

function Meter({ chat }: { chat: Chat }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const invalidateChats = useInvalidateChats();
  const answering = useAuiState((s) => s.thread.isRunning);
  // Compacting asks a model, which a member who only reads may not do.
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  const asks = me.data?.role === "owner" || me.data?.role === "ops";
  const compact = useMutation({
    // With how full the chat was, to tell afterwards whether anything was written.
    mutationFn: async () => ({ before: chat.context_tokens, after: await compactChat(chat.id) }),
    onSettled: () => {
      invalidateChats();
      void queryClient.invalidateQueries({ queryKey: chatMessagesKey(chat.id) });
    },
  });

  const share = Math.min(1, chat.context_tokens / chat.context_window);
  const percent = Math.round(share * 100);
  const half = share >= 0.5;
  const busy = compact.isPending || chat.running || answering;
  // Asked again with nothing said since, the server writes no notes and the chat comes back as
  // full as it was: said here, in place of an action that does nothing.
  const nothing =
    compact.isSuccess &&
    compact.data.after.context_tokens === compact.data.before &&
    chat.context_tokens === compact.data.before;
  const label = compact.isPending
    ? t.assistant.context.compacting
    : compact.isError
      ? t.assistant.context.failed
      : t.assistant.context.used(percent);

  return (
    <DropdownMenu modal={false}>
      <Hint label={label}>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            data-slot="context-meter"
            aria-label={label}
            className={cn(
              "hover:bg-accent focus-visible:ring-ring flex h-7 items-center gap-1 rounded-full px-1 outline-none focus-visible:ring-2",
              compact.isError ? "text-danger-foreground" : half ? "text-warning-foreground" : "text-muted-foreground",
            )}
          >
            <svg viewBox="0 0 20 20" className={cn("size-5 -rotate-90", compact.isPending && "animate-pulse")} aria-hidden>
              <circle cx="10" cy="10" r={RADIUS} fill="none" stroke="currentColor" strokeWidth="2.5" opacity="0.2" />
              <circle
                cx="10"
                cy="10"
                r={RADIUS}
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                // A chat that has just begun still shows a mark.
                strokeDasharray={`${Math.max(share, 0.04) * AROUND} ${AROUND}`}
              />
            </svg>
            {half ? <span className="pe-1 text-xs tabular-nums">{percent}%</span> : null}
          </button>
        </DropdownMenuTrigger>
      </Hint>
      <DropdownMenuContent align="end" side="top" className="w-72">
        <p className="px-2 pt-1.5 text-sm font-medium">{t.assistant.context.used(percent)}</p>
        <p className="text-muted-foreground px-2 pt-1 pb-2 text-xs text-pretty">
          {half ? t.assistant.context.half : t.assistant.context.what}
        </p>
        {compact.isError ? (
          <p className="text-danger-foreground px-2 pb-2 text-xs" role="alert">
            {t.assistant.context.failed}
          </p>
        ) : null}
        {nothing ? (
          <p className="text-muted-foreground px-2 pb-2 text-xs" role="status">
            {t.assistant.context.nothing}
          </p>
        ) : asks ? (
          <DropdownMenuItem disabled={busy} onSelect={() => compact.mutate()}>
            {compact.isPending ? t.assistant.context.compacting : t.assistant.context.compact}
          </DropdownMenuItem>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
