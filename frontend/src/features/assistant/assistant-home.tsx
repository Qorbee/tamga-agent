"use client";

import { AssistantRuntimeProvider } from "@assistant-ui/react";

import { Thread } from "@/components/assistant-ui/elements/thread.aui";
import { useI18n } from "@/lib/i18n/client";

import { usePlaceholderRuntime } from "./placeholder-runtime";
import { Welcome } from "./welcome";

/** The owner app's home: the assistant chat. Cards from tool calls will render inside `Thread`. */
export function AssistantHome() {
  const { t } = useI18n();
  const runtime = usePlaceholderRuntime(t.assistant.notConnected);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread components={{ Welcome }} placeholder={t.assistant.placeholder} />
    </AssistantRuntimeProvider>
  );
}
