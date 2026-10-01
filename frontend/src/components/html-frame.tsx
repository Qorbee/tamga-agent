"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { HELD_IMAGE } from "@/lib/safe-html";

// The frame loads nothing but images and fonts the app already holds: a picture on the web, a URL in
// the page's own CSS, or anything the sanitizer missed, is refused by the browser. Asked for, the
// page's pictures and fonts on the web load too, and nothing else does.
const framePolicy = (remote: boolean) => {
  const web = remote ? " https: http:" : "";
  return `default-src 'none'; img-src data: blob:${web}; font-src data:${web}; style-src 'unsafe-inline'`;
};
// A page is laid out for a white page and the browser's default type, as every mail client shows
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
 * Sanitized HTML (a mail's body, an HTML file) as its sender laid it out, as tall as its content.
 * The backend sanitized it; the frame adds a second wall: no script runs (no `allow-scripts`), a
 * link opens in a new tab and cannot navigate the app, and nothing reaches the network but the
 * page's pictures on the web, and those only when `remote` (the reader asked for them).
 */
export function HtmlFrame({ html, body, remote, title }: { html: string; body: string; remote: boolean; title: string }) {
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
      title={title}
      srcDoc={frameDocument(html, body, remote)}
      sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
      onLoad={measure}
      style={{ height }}
      className="ring-border block w-full rounded-xl ring-1"
    />
  );
}
