"use client";

import { useQueries, type UseQueryResult } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { getDocumentFile, type Message } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";

import { splitQuotedHtml } from "./quoted-html";

// The frame loads nothing but images the app already holds: a URL left in the mail's own CSS, or
// anything the sanitizer missed, is refused by the browser.
const FRAME_POLICY = "default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'";
// A mail is laid out for a white page, as every mail client shows one; its own CSS comes after.
const FRAME_STYLE =
  ":root{color-scheme:light}html,body{height:auto!important}html{overflow-x:auto}" +
  "body{margin:0;padding:16px;background:Canvas;color:CanvasText;font:15px/1.5 system-ui,sans-serif;overflow-wrap:break-word}" +
  "img{max-width:100%;height:auto}";

function frameDocument(body: string): string {
  return (
    `<!doctype html><html><head><meta charset="utf-8">` +
    `<meta http-equiv="Content-Security-Policy" content="${FRAME_POLICY}">` +
    `<base target="_blank"><style>${FRAME_STYLE}</style></head><body>${body}</body></html>`
  );
}

/**
 * A mail's HTML body as its sender laid it out, and the mails it quotes folded behind `toggle`. The
 * backend sanitized it; the frame adds a second wall: no script runs (no `allow-scripts`), a link
 * opens in a new tab and cannot navigate the app, and nothing reaches the network.
 */
export function HtmlBody({
  message,
  forward,
  toggle,
}: {
  message: Message;
  forward: boolean;
  toggle: (quoted: React.ReactNode) => React.ReactNode;
}) {
  const images = useInlineImages(message.inline_images);
  const split = useMemo(
    () => (images && message.body_html ? splitQuotedHtml(message.body_html, images, forward) : undefined),
    [images, message.body_html, forward],
  );
  if (!split) return <Skeleton className="h-24 rounded-xl" />;
  return (
    <>
      <MailFrame html={split.own} />
      {split.quoted !== null ? toggle(<MailFrame html={split.quoted} />) : null}
    </>
  );
}

function MailFrame({ html }: { html: string }) {
  const { t } = useI18n();
  const frame = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(0);
  const measure = useCallback(() => {
    const root = frame.current?.contentDocument?.documentElement;
    if (!root) return;
    const scrollbar = root.scrollWidth > root.clientWidth ? 16 : 0;
    setHeight(Math.ceil(root.getBoundingClientRect().height) + scrollbar);
  }, []);

  // Text reflows when the panel is resized, and the mail with it.
  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    let width = element.clientWidth;
    const observer = new ResizeObserver(() => {
      if (element.clientWidth === width) return;
      width = element.clientWidth;
      measure();
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [measure]);

  return (
    <iframe
      ref={frame}
      title={t.mail.body}
      srcDoc={frameDocument(html)}
      sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
      onLoad={measure}
      style={{ height }}
      className="ring-border block w-full rounded-xl ring-1"
    />
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
