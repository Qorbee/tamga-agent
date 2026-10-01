"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";

import { listChats } from "./api";

export const CHATS_KEY = ["chats"] as const;
export const chatMessagesKey = (id: string) => ["chat-messages", id] as const;

/** How often the list is read again: quickly while Tamga answers somewhere, else now and then. */
const WHILE_ANSWERING = 3_000;
const AT_REST = 30_000;

/**
 * The company's chats that are not archived, those this user has not read first. Until the
 * server pushes its events, the list is read again on a timer, so a chat Tamga wrote in shows
 * as unread without a reload.
 */
export function useChats() {
  return useQuery({
    queryKey: CHATS_KEY,
    queryFn: () => listChats({}),
    refetchInterval: (query) =>
      query.state.data?.items.some((chat) => chat.running) ? WHILE_ANSWERING : AT_REST,
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
