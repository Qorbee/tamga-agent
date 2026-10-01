"use client";

import { useQueries, type UseQueryResult } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { HtmlFrame } from "@/components/html-frame";
import { Skeleton } from "@/components/ui/skeleton";
import { getDocumentFile, type Message } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";

import { splitQuotedHtml } from "./quoted-html";

/**
 * A mail's HTML body as its sender laid it out (`HtmlFrame`), and the mails it quotes folded behind
 * `toggle`. Pictures on the web load only when `remote` (the reader asked for them).
 */
export function HtmlBody({
  message,
  forward,
  remote,
  toggle,
}: {
  message: Message;
  forward: boolean;
  remote: boolean;
  toggle: (quoted: React.ReactNode) => React.ReactNode;
}) {
  const { t } = useI18n();
  const images = useInlineImages(message.inline_images);
  const split = useMemo(
    () => (images && message.body_html ? splitQuotedHtml(message.body_html, images, forward, remote) : undefined),
    [images, message.body_html, forward, remote],
  );
  if (!split) return <Skeleton className="h-24 rounded-xl" />;
  return (
    <>
      <HtmlFrame html={split.own} body={split.body} remote={remote} title={t.mail.body} />
      {split.quoted !== null ? toggle(<HtmlFrame html={split.quoted} body={split.body} remote={remote} title={t.mail.body} />) : null}
    </>
  );
}

type Files = { settled: boolean; blobs: (Blob | undefined)[] };

// Stable, so TanStack Query recomputes it only when a file arrives.
const combineFiles = (results: UseQueryResult<Blob>[]): Files => ({
  settled: results.every((result) => !result.isPending),
  blobs: results.map((result) => result.data),
});

/**
 * An object URL for each image the body shows, by Content-ID, once every file has arrived or failed.
 * A file is fetched once and kept, so a logo in every mail of a thread is one request.
 */
function useInlineImages(inline: Message["inline_images"]): Record<string, string> | undefined {
  const entries = useMemo(() => Object.entries(inline), [inline]);
  const files = useQueries({
    queries: entries.map(([, id]) => ({
      queryKey: ["document-file", id],
      queryFn: () => getDocumentFile(id),
      staleTime: Infinity,
    })),
    combine: combineFiles,
  });
  const [urls, setUrls] = useState<{ files: Files; byCid: Record<string, string> }>();

  useEffect(() => {
    if (!files.settled) return;
    const byCid: Record<string, string> = {};
    entries.forEach(([cid], index) => {
      const blob = files.blobs[index];
      // A file the backend would not vouch for as an image arrives as a download, never drawn.
      if (blob?.type.startsWith("image/")) byCid[cid] = URL.createObjectURL(blob);
    });
    // The URLs are browser resources that must outlive the render that made them.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setUrls({ files, byCid });
    return () => Object.values(byCid).forEach((url) => URL.revokeObjectURL(url));
  }, [entries, files]);

  return urls?.files === files ? urls.byCid : undefined;
}
