"use client";

import { AssistantRuntimeProvider, useAui, useAuiState } from "@assistant-ui/react";
import { useEffect, type ReactNode } from "react";

import { useI18n } from "@/lib/i18n/client";

import { usePlaceholderRuntime } from "./placeholder-runtime";

/** Titles longer than this are cut; the full first message is still in the chat. */
const TITLE_LENGTH = 60;

/**
 * One chat runtime for the whole owner app: the sidebar's chat list and the chat page read the same
 * threads, and a chat keeps its place while the owner looks at another page.
 */
export function ChatRuntime({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const runtime = usePlaceholderRuntime(t.assistant.notConnected);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <NameAfterFirstMessage />
      {children}
    </AssistantRuntimeProvider>
  );
}

/**
 * PLACEHOLDER, with the runtime — the chat service titles its threads. Until then a chat takes its
 * first message as its name, so the sidebar lists something better than "New chat" several times.
 */
function NameAfterFirstMessage() {
  const aui = useAui();
  const status = useAuiState((s) => s.threadListItem.status);
  const title = useAuiState((s) => s.threadListItem.title);
  const firstText = useAuiState((s) => {
    const first = s.thread.messages.find((message) => message.role === "user");
    const part = first?.content.find((content) => content.type === "text");
    return part?.type === "text" ? part.text.trim() : "";
  });

  useEffect(() => {
    // A thread becomes "regular" once its first message is sent; a new one cannot be renamed.
    if (status !== "regular" || title || !firstText) return;
    const name = firstText.length > TITLE_LENGTH ? `${firstText.slice(0, TITLE_LENGTH - 1)}…` : firstText;
    aui.threadListItem.rename(name);
  }, [aui, status, title, firstText]);

  return null;
}
