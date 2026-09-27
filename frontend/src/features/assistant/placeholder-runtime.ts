"use client";

import { useLocalRuntime, type ChatModelAdapter } from "@assistant-ui/react";
import { useMemo } from "react";

/**
 * PLACEHOLDER — replaced by the AG-UI runtime (`useAgUiRuntime`, ADR-0006) when the chat service
 * lands on Oct 1. Answers every message with one fixed notice; it calls no model and sends nothing
 * over the network, so the shell and its styling can be exercised before the backend exists.
 */
export function usePlaceholderRuntime(notice: string) {
  const adapter = useMemo<ChatModelAdapter>(
    () => ({
      async run() {
        return { content: [{ type: "text", text: notice }] };
      },
    }),
    [notice],
  );
  return useLocalRuntime(adapter);
}
