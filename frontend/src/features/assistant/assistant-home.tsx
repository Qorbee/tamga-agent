"use client";

import { useAuiState } from "@assistant-ui/react";

import { Thread } from "@/components/assistant-ui/elements/thread.aui";
import { PageHeader } from "@/components/page-header";
import { useI18n } from "@/lib/i18n/client";

import { ChatTitle } from "./chat-title";
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
      <PageHeader title={started ? <ChatTitle /> : undefined} />
      <div className="min-h-0 flex-1">
        <Thread components={{ Welcome }} placeholder={t.assistant.placeholder} />
      </div>
    </div>
  );
}
