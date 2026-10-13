"use client";

import { useAuiState } from "@assistant-ui/react";
import { useEffect } from "react";

import { Thread } from "@/components/assistant-ui/elements/thread.aui";
import { LoaderIcon } from "@/components/icons";
import { PageHeader } from "@/components/page-header";
import { markChatRead } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";
import { useChats, useInvalidateChats } from "@/lib/use-chats";

import { useChatShown } from "./chat-runtime";
import { ChatTitle } from "./chat-title";
import { ContextMeter } from "./context-meter";
import { RunChoiceChip } from "./run-choice-chip";
import { Welcome } from "./welcome";

/**
 * The owner app's home: the open chat, under a bar with its name and menu. The runtime is the app
 * layout's (`ChatRuntime`), shared with the sidebar's chat list. Cards from tool calls will render in
 * `Thread`.
 */
export function AssistantHome() {
  const { t } = useI18n();
  const started = useAuiState((s) => s.thread.messages.length > 0);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ReadWhenShown />
      <PageHeader title={started ? <ChatTitle /> : undefined} />
      <div className="min-h-0 flex-1">
        <Thread components={{ Welcome, ComposerTools, AfterMessages: WorkingHere }} placeholder={t.assistant.placeholder} />
      </div>
    </div>
  );
}

/**
 * While Tamga writes in the open chat and this page is not the one asking (it began a turn on
 * its own, or answers someone else), a line says so until the message lands: nothing appears
 * out of nowhere.
 */
function WorkingHere() {
  const { t } = useI18n();
  const id = useAuiState((s) => s.threadListItem.id);
  const answering = useAuiState((s) => s.thread.isRunning);
  const chat = useChats().data?.items.find((item) => item.id === id);
  if (!chat?.running || answering) return null;
  return (
    <p data-slot="chat-working" className="text-muted-foreground flex items-center gap-2 text-sm" aria-live="polite">
      <LoaderIcon className="size-3.5 shrink-0 animate-spin [animation-duration:0.8s]" aria-hidden />
      <span className="shimmer motion-reduce:animate-none">{t.assistant.working}</span>
    </p>
  );
}

/** What the app puts in the composer beside Send. */
function ComposerTools() {
  return (
    <>
      <RunChoiceChip />
      <ContextMeter />
    </>
  );
}

/**
 * The open chat is read once it is on screen with nothing still being written in it: the chat
 * page is showing, in a window the user is looking at.
 */
function ReadWhenShown() {
  useChatShown();
  const id = useAuiState((s) => s.threadListItem.id);
  const answering = useAuiState((s) => s.thread.isRunning);
  const chat = useChats().data?.items.find((item) => item.id === id);
  const invalidateChats = useInvalidateChats();
  const unread = chat?.unread === true && !chat.running && !answering;

  useEffect(() => {
    if (!unread) return;
    const read = () => {
      if (document.visibilityState !== "visible") return;
      void markChatRead(id).then(invalidateChats, () => undefined);
    };
    read();
    document.addEventListener("visibilitychange", read);
    return () => document.removeEventListener("visibilitychange", read);
  }, [unread, id, invalidateChats]);

  return null;
}
