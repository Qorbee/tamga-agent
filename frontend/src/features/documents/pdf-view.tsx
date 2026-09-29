"use client";

import type { PDFDocumentLoadingTask, PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import { useEffect, useRef, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { useI18n } from "@/lib/i18n/client";

/**
 * pdf.js draws the pages itself, so a PDF shows the same on every phone: Chrome on Android shows no
 * PDF inside a page. It loads on first use, and its worker parses the file off the main thread.
 */
async function openPdf(file: Blob): Promise<PDFDocumentLoadingTask> {
  const pdfjs = await import("pdfjs-dist");
  pdfjs.GlobalWorkerOptions.workerSrc ||= new URL(
    "pdfjs-dist/build/pdf.worker.min.mjs",
    import.meta.url,
  ).toString();
  return pdfjs.getDocument({
    data: new Uint8Array(await file.arrayBuffer()),
    // Served from public/pdfjs (scripts/copy-pdfjs-assets.mjs): fonts a PDF does not embed, the
    // maps its CID fonts need, and the decoders for scanned (JBIG2, JPEG 2000) pages.
    cMapUrl: "/pdfjs/cmaps/",
    cMapPacked: true,
    standardFontDataUrl: "/pdfjs/standard_fonts/",
    wasmUrl: "/pdfjs/wasm/",
    iccUrl: "/pdfjs/iccs/",
  });
}

/** Every page, as wide as the panel, each drawn when it scrolls near. */
export function PdfView({ file, onFailed }: { file: Blob; onFailed: () => void }) {
  const { t } = useI18n();
  const holder = useRef<HTMLDivElement>(null);
  const [pdf, setPdf] = useState<PDFDocumentProxy>();
  const width = useWidth(holder);

  useEffect(() => {
    let cancelled = false;
    let loading: PDFDocumentLoadingTask | undefined;
    openPdf(file)
      .then((task) => {
        loading = task;
        if (cancelled) void task.destroy();
        return task.promise;
      })
      .then(
        (document) => {
          if (!cancelled) setPdf(document);
        },
        () => {
          if (!cancelled) onFailed();
        },
      );
    // Destroying the loading task ends its worker and frees the document.
    return () => {
      cancelled = true;
      void loading?.destroy();
    };
  }, [file, onFailed]);

  return (
    <div ref={holder} className="grid gap-3">
      {pdf && width > 0 ? (
        <>
          <p className="text-muted-foreground text-xs tabular-nums">{t.files.pages(pdf.numPages)}</p>
          {Array.from({ length: pdf.numPages }, (_, index) => (
            <PdfPage key={index} pdf={pdf} number={index + 1} width={width} />
          ))}
        </>
      ) : (
        <Skeleton className="aspect-[1/1.414] w-full rounded-md" />
      )}
    </div>
  );
}

/** One page; one that cannot be drawn says so, and the pages around it still show. */
function PdfPage({ pdf, number, width }: { pdf: PDFDocumentProxy; number: number; width: number }) {
  const { t } = useI18n();
  const holder = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  // A4 until the page says otherwise, so the scrollbar is about right before anything is drawn.
  const [ratio, setRatio] = useState(1.414);
  const [near, setNear] = useState(number <= 2);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const element = holder.current;
    if (near || !element) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) setNear(true);
      },
      { rootMargin: "800px 0px" },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [near]);

  useEffect(() => {
    if (!near) return;
    let cancelled = false;
    let task: RenderTask | undefined;
    pdf
      .getPage(number)
      .then((page) => {
        const target = canvas.current;
        if (cancelled || !target) return;
        const natural = page.getViewport({ scale: 1 });
        setRatio(natural.height / natural.width);
        const viewport = page.getViewport({ scale: (width / natural.width) * (window.devicePixelRatio || 1) });
        target.width = Math.floor(viewport.width);
        target.height = Math.floor(viewport.height);
        task = page.render({ canvas: target, viewport });
        return task.promise;
      })
      .then(
        () => setFailed(false),
        // Only this effect's cleanup cancels a drawing, and a cancelled one is not a failure.
        () => {
          if (!cancelled) setFailed(true);
        },
      );
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [pdf, number, width, near]);

  return (
    <div
      ref={holder}
      style={{ height: width * ratio }}
      className="bg-background ring-border relative overflow-hidden rounded-sm shadow-sm ring-1"
    >
      <canvas ref={canvas} className="block h-full w-full" />
      {failed ? (
        <p className="text-muted-foreground absolute inset-0 grid place-items-center p-6 text-center text-sm">
          {t.files.pageFailed}
        </p>
      ) : null}
    </div>
  );
}

/** The element's content width, settled a moment after a resize so pages are not redrawn per pixel. */
function useWidth(ref: React.RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let first = true;
    const observer = new ResizeObserver(([entry]) => {
      const next = Math.floor(entry?.contentRect.width ?? 0);
      clearTimeout(timer);
      if (first) {
        first = false;
        setWidth(next);
      } else timer = setTimeout(() => setWidth(next), 150);
    });
    observer.observe(element);
    return () => {
      clearTimeout(timer);
      observer.disconnect();
    };
  }, [ref]);
  return width;
}
