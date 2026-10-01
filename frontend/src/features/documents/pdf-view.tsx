"use client";

import type { PDFDocumentLoadingTask, PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";

import { clamp, useWheelZoom, ViewerToolbar, ZOOM_STEP } from "./viewer-toolbar";

/**
 * pdf.js draws the pages itself, so a PDF shows the same on every phone: Chrome on Android shows no
 * PDF inside a page. It loads on first use, and its worker parses the file off the main thread.
 *
 * Its legacy build: the modern one calls JavaScript only the newest browsers have
 * (`Map.getOrInsertComputed`), and in any other every page fails to draw. The legacy build carries
 * those as polyfills.
 */
async function openPdf(file: Blob): Promise<PDFDocumentLoadingTask> {
  const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
  pdfjs.GlobalWorkerOptions.workerSrc ||= new URL(
    "pdfjs-dist/legacy/build/pdf.worker.min.mjs",
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

const MIN_ZOOM = 0.5;
const MAX_ZOOM = 4;
// A page's drawing waits this long after the last zoom step, so a pinch redraws once, not per step.
const REDRAW_AFTER = 150;

/**
 * Every page, as wide as the panel at 100%, each drawn when it scrolls near. Zoomed with the buttons
 * or the wheel with Ctrl (a trackpad's pinch), keeping the spot under the pointer in place, and
 * turned a quarter at a time for a sideways scan.
 */
export function PdfView({ file, onFailed }: { file: Blob; onFailed: () => void }) {
  const { t, locale } = useI18n();
  const scroller = useRef<HTMLDivElement>(null);
  const [pdf, setPdf] = useState<PDFDocumentProxy>();
  const width = useWidth(scroller);
  const [zoom, setZoom] = useState(1);
  const [rotation, setRotation] = useState(0);
  const current = useCurrentPage(scroller, pdf && width > 0 ? pdf.numPages : 0);
  // Where the scroll should land once the zoomed pages are laid out.
  const landing = useRef<{ left: number; top: number } | null>(null);
  const zoomRef = useRef(zoom);

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

  /** Zooms keeping the point `anchor` (in the visible area, from its top left) where it is. */
  const zoomTo = useCallback((next: number, anchor?: { x: number; y: number }) => {
    const element = scroller.current;
    const target = clamp(next, MIN_ZOOM, MAX_ZOOM);
    const ratio = target / zoomRef.current;
    if (element && ratio !== 1) {
      const point = anchor ?? { x: element.clientWidth / 2, y: element.clientHeight / 2 };
      landing.current = {
        left: (element.scrollLeft + point.x) * ratio - point.x,
        top: (element.scrollTop + point.y) * ratio - point.y,
      };
    }
    zoomRef.current = target;
    setZoom(target);
  }, []);

  useLayoutEffect(() => {
    const element = scroller.current;
    if (!element || !landing.current) return;
    element.scrollTo(landing.current);
    landing.current = null;
  }, [zoom]);

  useWheelZoom(scroller, (factor, point) => zoomTo(zoomRef.current * factor, point));

  const pageWidth = Math.floor(width * zoom);
  return (
    <div className="bg-muted/50 relative h-full">
      <div ref={scroller} className="h-full overflow-auto p-3 pb-16 md:p-5 md:pb-16">
        {pdf && width > 0 ? (
          <div className="mx-auto grid w-max gap-3">
            {Array.from({ length: pdf.numPages }, (_, index) => (
              <PdfPage key={index} pdf={pdf} number={index + 1} width={pageWidth} rotation={rotation} />
            ))}
          </div>
        ) : (
          <Skeleton className="aspect-[1/1.414] w-full rounded-md" />
        )}
      </div>
      {pdf ? (
        <ViewerToolbar
          zoom={zoom}
          canZoomIn={zoom < MAX_ZOOM}
          canZoomOut={zoom > MIN_ZOOM}
          onZoomIn={() => zoomTo(zoom * ZOOM_STEP)}
          onZoomOut={() => zoomTo(zoom / ZOOM_STEP)}
          onFit={() => zoomTo(1)}
          onRotate={() => setRotation((turn) => (turn + 90) % 360)}
          className="absolute bottom-3 left-1/2 -translate-x-1/2"
        >
          <span className="text-muted-foreground px-2 text-xs whitespace-nowrap tabular-nums">
            {t.files.viewer.page(formatNumber(current, locale), formatNumber(pdf.numPages, locale))}
          </span>
        </ViewerToolbar>
      ) : null}
    </div>
  );
}

/** One page; one that cannot be drawn says so, and the pages around it still show. */
function PdfPage({
  pdf,
  number,
  width,
  rotation,
}: {
  pdf: PDFDocumentProxy;
  number: number;
  width: number;
  rotation: number;
}) {
  const { t } = useI18n();
  const holder = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  // A4 until the page says otherwise, so the scrollbar is about right before anything is drawn.
  const [ratio, setRatio] = useState(1.414);
  const [near, setNear] = useState(number <= 2);
  const [failed, setFailed] = useState(false);
  // The box follows the zoom at once; the drawing, stretched meanwhile, catches up when it settles.
  const drawWidth = useSettled(width, REDRAW_AFTER);

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
        const turn = (page.rotate + rotation) % 360;
        const natural = page.getViewport({ scale: 1, rotation: turn });
        setRatio(natural.height / natural.width);
        const viewport = page.getViewport({
          scale: (drawWidth / natural.width) * (window.devicePixelRatio || 1),
          rotation: turn,
        });
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
  }, [pdf, number, drawWidth, rotation, near]);

  return (
    <div
      ref={holder}
      data-page={number}
      style={{ width, height: width * ratio }}
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

/** The page across the middle of the visible area, counted from 1. */
function useCurrentPage(scroller: React.RefObject<HTMLElement | null>, pages: number): number {
  const [current, setCurrent] = useState(1);
  useEffect(() => {
    const root = scroller.current;
    if (!root || pages === 0) return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const number = Number((entry.target as HTMLElement).dataset.page);
          if (entry.isIntersecting && number) setCurrent(number);
        }
      },
      { root, rootMargin: "-50% 0px -50% 0px" },
    );
    root.querySelectorAll("[data-page]").forEach((page) => observer.observe(page));
    return () => observer.disconnect();
  }, [scroller, pages]);
  return current;
}

/** `value`, once it has stopped changing for `delay` milliseconds. */
function useSettled<T>(value: T, delay: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
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
