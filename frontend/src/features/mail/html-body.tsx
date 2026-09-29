"use client";

import { useQueries, type UseQueryResult } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { getDocumentFile, type Message } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";

import { HELD_IMAGE, splitQuotedHtml } from "./quoted-html";

// The frame loads nothing but images and fonts the app already holds: a picture on the web, a URL in
// the mail's own CSS, or anything the sanitizer missed, is refused by the browser. Asked for, the
// mail's pictures and fonts on the web load too, and nothing else does.
const framePolicy = (remote: boolean) => {
  const web = remote ? " https: http:" : "";
  return `default-src 'none'; img-src data: blob:${web}; font-src data:${web}; style-src 'unsafe-inline'`;
};
// A mail is laid out for a white page and the browser's default type, as every mail client shows
// one; its own CSS and its `<body>`'s attributes come after, and win. A picture outside a table is
// kept within the frame; one inside a table keeps its size, or its column would squeeze it.
const FRAME_STYLE =
  ":root{color-scheme:light}html,body{height:auto!important}html{overflow-x:auto}" +
  "body{margin:0;padding:16px;color:CanvasText;font:16px system-ui,sans-serif;overflow-wrap:break-word}" +
  "body:not([bgcolor]){background:Canvas}" +
  "img:not(table img){max-width:100%;height:auto}" +
  // A picture the frame will not load: a quiet box of its size, holding its alt text.
  `[${HELD_IMAGE}]{display:inline-flex;align-items:center;justify-content:center;box-sizing:border-box;` +
  "max-width:100%;min-height:24px;padding:4px 8px;overflow:hidden;vertical-align:middle;text-align:center;" +
  "border:1px dashed color-mix(in srgb,CanvasText 25%,Canvas);border-radius:4px;" +
  "background:color-mix(in srgb,CanvasText 4%,Canvas);color:GrayText;font:12px/1.4 system-ui,sans-serif}";

function frameDocument(html: string, body: string, remote: boolean): string {
  return (
    `<!doctype html><html><head><meta charset="utf-8">` +
    `<meta http-equiv="Content-Security-Policy" content="${framePolicy(remote)}">` +
    `<base target="_blank"><style>${FRAME_STYLE}</style></head><body${body}>${html}</body></html>`
  );
}

/**
 * A mail's HTML body as its sender laid it out, and the mails it quotes folded behind `toggle`. The
 * backend sanitized it; the frame adds a second wall: no script runs (no `allow-scripts`), a link
 * opens in a new tab and cannot navigate the app, and nothing reaches the network but the mail's
 * pictures on the web, and those only when `remote` (the reader asked for them).
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
  const images = useInlineImages(message.inline_images);
  const split = useMemo(
    () => (images && message.body_html ? splitQuotedHtml(message.body_html, images, forward, remote) : undefined),
    [images, message.body_html, forward, remote],
  );
  if (!split) return <Skeleton className="h-24 rounded-xl" />;
  return (
    <>
      <MailFrame html={split.own} body={split.body} remote={remote} />
      {split.quoted !== null ? toggle(<MailFrame html={split.quoted} body={split.body} remote={remote} />) : null}
    </>
  );
}

function MailFrame({ html, body, remote }: { html: string; body: string; remote: boolean }) {
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
      srcDoc={frameDocument(html, body, remote)}
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
