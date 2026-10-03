"use client";

import { useQuery } from "@tanstack/react-query";

import { getMe } from "./api";

/**
 * Whether the signed-in user may ask Tamga: a question costs money and reads everything the
 * company holds, so a member who only reads may not. The server decides; this only keeps a
 * control the server would refuse off the page.
 */
export function useAsks(): boolean {
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  return me.data?.role === "owner" || me.data?.role === "ops";
}

/** Whether the signed-in user may change a project: the owner and ops; a viewer only reads. */
export function useCanEditProjects(): boolean {
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  return me.data?.role === "owner" || me.data?.role === "ops";
}

/** Whether the signed-in user is the owner: only the owner merges projects and moves what is in them. */
export function useIsOwner(): boolean {
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  return me.data?.role === "owner";
}
