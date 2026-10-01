"use client";

import { ArrowLeft01Icon, Cancel01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

import { CanvasScope, useCanvasBack, useCloseCanvas } from "@/components/canvas-link";
import { ResizeHandle } from "@/components/resize-handle";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n/client";
import { CANVAS_SIZE, saveSize } from "@/lib/panel-sizes";
import { cn } from "@/lib/utils";

// The page beside the canvas keeps at least this much room; the canvas may take all the rest.
const PAGE_MIN = 400;
// The canvas's margin at its outer edge.
const GAP = 8;

/**
 * The canvas: a card floating beside the page, its left edge dragged to resize between its minimum
 * and all the room the page can give; full screen below the `lg` breakpoint. Only its close button
 * closes it. It slides in when a record opens and out when it closes, keeping the last record on
 * screen until it is gone; its content keeps the panel's width while it slides, so nothing reflows.
 * `content` is null while nothing is open.
 */
export function CanvasFrame({ initialWidth, content }: { initialWidth: number; content: ReactNode }) {
  const { t } = useI18n();
  const [width, setWidth] = useState(initialWidth);
  // The widest the canvas can be now: the row it shares with the page, less the page's minimum.
  // Null until measured.
  const [room, setRoom] = useState<number | null>(null);
  const frame = useRef<HTMLElement>(null);
  // The record shown, which outlives `content` for as long as the closing slide lasts.
  const [shown, setShown] = useState<ReactNode>(content);
  if (content !== null && content !== shown) setShown(content);
  const closing = content === null && shown !== null;

  // With reduced motion there is no animation to end, so the record also goes after the slide's time.
  useEffect(() => {
    if (!closing) return;
    const timer = setTimeout(() => setShown(null), 300);
    return () => clearTimeout(timer);
  }, [closing]);

  const open = shown !== null;
  useLayoutEffect(() => {
    const aside = frame.current;
    const row = aside?.parentElement;
    const page = aside?.previousElementSibling;
    if (!aside || !row || !page) return;
    // The page starts where the sidebar ends, so this follows the sidebar opening and closing too.
    const measure = () => {
      const free = row.getBoundingClientRect().right - page.getBoundingClientRect().left;
      setRoom(Math.max(CANVAS_SIZE.min, Math.floor(free - PAGE_MIN - GAP)));
    };
    const observer = new ResizeObserver(measure);
    observer.observe(row);
    observer.observe(page);
    measure();
    return () => observer.disconnect();
  }, [open]);

  if (!open) return null;
  const shownWidth = Math.min(width, room ?? width);
  return (
    <aside
      ref={frame}
      data-state={closing ? "closed" : "open"}
      style={{ "--canvas-width": `${shownWidth}px` } as CSSProperties}
      onAnimationEnd={(event) => {
        if (closing && event.target === event.currentTarget) setShown(null);
      }}
      className={cn(
        "relative flex shrink-0 flex-col overflow-hidden motion-reduce:animate-none",
        "max-lg:bg-card max-lg:fixed max-lg:inset-0 max-lg:z-40 max-lg:duration-200",
        "max-lg:data-[state=open]:animate-in max-lg:data-[state=open]:fade-in max-lg:data-[state=open]:slide-in-from-bottom-6",
        "max-lg:data-[state=closed]:animate-out max-lg:data-[state=closed]:fade-out max-lg:data-[state=closed]:slide-out-to-bottom-6 max-lg:data-[state=closed]:fill-mode-forwards",
        "lg:bg-card lg:border-border lg:m-2 lg:ms-0 lg:w-(--canvas-width) lg:rounded-2xl lg:border lg:shadow-lg",
        "lg:data-[state=open]:animate-canvas-open lg:data-[state=closed]:animate-canvas-close",
      )}
    >
      <ResizeHandle
        label={t.shell.resizeCanvas}
        size={{ ...CANVAS_SIZE, max: room ?? CANVAS_SIZE.max }}
        width={shownWidth}
        edge="left"
        onResize={setWidth}
        onResizeEnd={(settled) => saveSize(CANVAS_SIZE, settled)}
        className="max-lg:hidden"
      />
      <div className="flex min-h-0 flex-1 flex-col lg:w-(--canvas-width) lg:self-end">{shown}</div>
    </aside>
  );
}

/**
 * A record open in the canvas: its title, its actions (a download), a close button and the record,
 * which scrolls on its own. Closing leaves the page under it as it was; a record opened from another
 * in the canvas (a mail's attachment) has a back button to it. `title` can load with the record, so
 * the panel is named by `label` until it has.
 */
export function CanvasPanel({
  label,
  title,
  actions,
  children,
}: {
  label: string;
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  const { t } = useI18n();
  const close = useCloseCanvas();
  const back = useCanvasBack();

  return (
    <section
      data-slot="canvas-panel"
      aria-label={label}
      className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[inherit]"
    >
      <header className={cn("border-border flex h-12 shrink-0 items-center gap-1 border-b pe-2", back ? "ps-2" : "ps-4")}>
        {back ? (
          <Button variant="ghost" size="icon-sm" onClick={back.go}>
            <HugeiconsIcon icon={ArrowLeft01Icon} strokeWidth={1.75} />
            <span className="sr-only">{t.shell.backTo[back.view.kind]}</span>
          </Button>
        ) : null}
        <h2 className="flex min-w-0 flex-1 items-baseline gap-1.5 text-sm font-medium">{title ?? label}</h2>
        {actions}
        <Button variant="ghost" size="icon-sm" onClick={close}>
          <HugeiconsIcon icon={Cancel01Icon} strokeWidth={1.75} />
          <span className="sr-only">{t.shell.closeCanvas}</span>
        </Button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <CanvasScope>{children}</CanvasScope>
      </div>
    </section>
  );
}
