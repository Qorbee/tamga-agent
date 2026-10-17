"use client";

import { useEffect } from "react";

import { useI18n } from "@/lib/i18n/client";

// What the tab names: a record open in the canvas, else the page's own subject (the open chat,
// or the page's name). The owner app's routes give no title of their own, but Next writes the
// root's default again on a navigation, after the page has named itself; so the title is put
// back whenever anything else changes it, while something here names it.
type Slot = "page" | "record";
const shown: Record<Slot, string | null> = { page: null, record: null };
let wanted: string | null = null;
let watching: MutationObserver | null = null;

function apply(product: string) {
  const subject = shown.record ?? shown.page;
  wanted = subject ? `${subject} · ${product}` : null;
  // Nothing here names it (the owner app was left): the page's own title stands.
  if (!wanted) {
    watching?.disconnect();
    watching = null;
    return;
  }
  document.title = wanted;
  if (watching) return;
  watching = new MutationObserver(() => {
    if (wanted && document.title !== wanted) document.title = wanted;
  });
  watching.observe(document.head, { childList: true, subtree: true, characterData: true });
}

/** Names the browser tab after `subject` while the caller is mounted, as a chat app does. */
export function useTabTitle(subject: string | null | undefined, slot: Slot = "page") {
  const { t } = useI18n();
  const product = t.meta.title;
  useEffect(() => {
    shown[slot] = subject?.trim() || null;
    apply(product);
    return () => {
      shown[slot] = null;
      apply(product);
    };
  }, [subject, slot, product]);
}

/** `useTabTitle` for a server component's page. */
export function TabTitle({ subject }: { subject: string }) {
  useTabTitle(subject);
  return null;
}
