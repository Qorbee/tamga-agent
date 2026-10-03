"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";

import { getChatMessages, listChats } from "./api";

export const CHATS_KEY = ["chats"] as const;
export const chatMessagesKey = (id: string) => ["chat-messages", id] as const;

/** How long messages read ahead of a chat's opening are fresh enough not to be read again for it. */
const READ_AHEAD = 30_000;

/** How often the list is read again while Tamga answers somewhere. */
const WHILE_ANSWERING = 3_000;

/**
 * The company's chats that are not archived, those this user has not read first. The company
 * event stream says when one changes (`features/shell/company-events.tsx`); while a chat is
 * being answered the list is also read on a timer, for the end of a run on another device.
 */
export function useChats() {
  return useQuery({
    queryKey: CHATS_KEY,
    queryFn: () => listChats({}),
    refetchInterval: (query) =>
      query.state.data?.items.some((chat) => chat.running) ? WHILE_ANSWERING : false,
  });
}

/** The chats put away: off the list, kept, and brought back from the sidebar's Archived group. */
export function useArchivedChats() {
  return useQuery({
    queryKey: [...CHATS_KEY, "archived"],
    queryFn: () => listChats({ archived: true }),
  });
}

export function useInvalidateChats(): () => void {
  const queryClient = useQueryClient();
  return () => void queryClient.invalidateQueries({ queryKey: CHATS_KEY });
}

/**
 * Reads a chat's messages before it is opened, while the pointer or focus is on its row, so the
 * chat opens with them instead of standing empty while they load.
 */
export function useReadAhead() {
  const queryClient = useQueryClient();
  return (id: string) =>
    void queryClient.prefetchQuery({
      queryKey: chatMessagesKey(id),
      queryFn: () => getChatMessages(id),
      staleTime: READ_AHEAD,
    });
}
