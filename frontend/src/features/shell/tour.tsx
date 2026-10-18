"use client";

import { useQuery } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { Button } from "@/components/ui/button";
import { getMe } from "@/lib/api";
import type { Dictionary } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/client";
import { TOUR_EVENT } from "@/lib/tour";
import { cn } from "@/lib/utils";

import { CHAT_PATH } from "./pages";

type StepKey = keyof Dictionary["tour"]["steps"];

type Step = {
  key: StepKey;
  // The elements it points at by their `data-tour`, the first on screen lit: a place in the
  // sidebar, else the button that opens it on a phone. A step with none on screen is told in a
  // card alone.
  targets?: string[];
  // The page it is told on: the tour goes there itself, a pointer first tapping `via` (the
  // sidebar's row) where it is on screen, so the viewer sees how to get there again.
  route?: string;
  via?: string;
  // Its place is a whole page, lit from the top as far as the card leaves room.
  page?: boolean;
  sandbox?: boolean;
};

const STEPS: Step[] = [
  { key: "welcome" },
  { key: "composer", targets: ["composer"], route: CHAT_PATH, via: "new-chat" },
  { key: "chats", targets: ["chats", "menu"] },
  { key: "projects", targets: ["projects-page"], route: "/app/projects", via: "nav-projects", page: true },
  { key: "payments", targets: ["payments-page"], route: "/app/payments", via: "nav-payments", page: true },
  { key: "log", targets: ["log-page"], route: "/app/log", via: "nav-log", page: true },
  { key: "clock", targets: ["skip-day"], sandbox: true },
  { key: "world", targets: ["world"], sandbox: true },
  { key: "account", targets: ["account", "menu"] },
];

// Kept in this browser only: whether its viewer has been shown the tour.
const SEEN_KEY = "tamga_tour_seen";
const PAD = 8;
const GAP = 12;
// The least room kept between a ring and the screen's edge.
const EDGE = 3;
// The pointer's glide to the row it taps, the tap, and the page's change after it.
const GLIDE = 750;
const TAP = 220;

function seen(): boolean {
  try {
    return window.localStorage.getItem(SEEN_KEY) === "1";
  } catch {
    // Storage refused: shown as to a first visitor, even if that means again next time.
    return false;
  }
}

function markSeen() {
  try {
    window.localStorage.setItem(SEEN_KEY, "1");
  } catch {
    // Storage refused (a private window): the tour may show again, which is harmless.
  }
}

/** Where the first of a step's elements on screen is, or null when none is. */
function place(targets: string[] | undefined): DOMRect | null {
  for (const target of targets ?? []) {
    for (const element of document.querySelectorAll<HTMLElement>(`[data-tour="${target}"]`)) {
      const rect = element.getBoundingClientRect();
      const inside = rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth;
      if (rect.width > 0 && rect.height > 0 && inside) return rect;
    }
  }
  return null;
}

/**
 * A walk through the app, one place at a time: the place lit and the rest dimmed, a card beside
 * it saying what it does and what to try, with Back, Next and Skip (Escape skips, the arrows
 * step). It opens by itself the first time a sandbox is opened in this browser, and again from
 * the account menu or the playground's bar.
 */
export function Tour() {
  const { t } = useI18n();
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  const sandbox = me.data?.company.kind === "sandbox";
  const router = useRouter();
  const pathname = usePathname();
  const path = useRef(pathname);
  const [at, setAt] = useState<number | null>(null);
  const [rect, setRect] = useState<DOMRect | null>(null);
  const [pointer, setPointer] = useState<{ x: number; y: number; tap: boolean } | null>(null);
  const card = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const titleId = useId();
  const steps = STEPS.filter((step) => !step.sandbox || sandbox);
  const step = at === null ? null : steps[at];

  // The first sandbox opened in this browser starts it, once the page has settled.
  useEffect(() => {
    if (!sandbox || seen()) return;
    // Seen again when it fires: the tour may have been opened and skipped meanwhile.
    const timer = window.setTimeout(() => setAt((open) => (open !== null || seen() ? open : 0)), 1200);
    return () => window.clearTimeout(timer);
  }, [sandbox]);

  useEffect(() => {
    const open = () => setAt(0);
    window.addEventListener(TOUR_EVENT, open);
    return () => window.removeEventListener(TOUR_EVENT, open);
  }, []);

  useEffect(() => {
    path.current = pathname;
  }, [pathname]);

  // A step told on another page goes there: the pointer glides from the card to the sidebar's
  // row and taps it, then the page changes. Without the row on screen (a phone's folded menu) or
  // with motion turned off, the page simply changes.
  useEffect(() => {
    const route = step?.route;
    if (!route || path.current === route) return;
    const via = step.via ? place([step.via]) : null;
    const timers: number[] = [];
    const later = (ms: number, run: () => void) => timers.push(window.setTimeout(run, ms));
    if (!via || matchMedia("(prefers-reduced-motion: reduce)").matches) {
      later(0, () => router.push(route));
    } else {
      const from = card.current?.getBoundingClientRect();
      later(0, () =>
        setPointer({
          x: from ? from.left + from.width / 2 : innerWidth / 2,
          y: from ? from.top + 32 : innerHeight / 2,
          tap: false,
        }),
      );
      later(40, () => setPointer({ x: via.left + Math.min(28, via.width / 2), y: via.top + via.height / 2, tap: false }));
      later(40 + GLIDE, () => setPointer((was) => was && { ...was, tap: true }));
      later(40 + GLIDE + TAP, () => router.push(route));
      later(40 + GLIDE + TAP + 500, () => setPointer(null));
    }
    return () => {
      for (const timer of timers) window.clearTimeout(timer);
      setPointer(null);
    };
  }, [step, router]);

  // The lit place follows the page while the tour is open: a sidebar sliding, a list loading.
  useLayoutEffect(() => {
    if (!step) return;
    let frame = 0;
    // Nothing yet, so the first read is always taken, even "nothing lit".
    let last: string | null = null;
    // Until the step's page has come, the row that leads there is lit.
    const targets = [...(step.targets ?? []), ...(step.via ? [step.via] : [])];
    const follow = () => {
      const next = place(targets);
      // The screen's size too: a card with nothing lit is placed by it, and a phone turns.
      const key = `${innerWidth}x${innerHeight}:` + (next ? `${next.x},${next.y},${next.width},${next.height}` : "");
      if (key !== last) {
        last = key;
        setRect(next);
      }
      if (card.current) {
        const { offsetWidth: width, offsetHeight: height } = card.current;
        setSize((was) => (was.width === width && was.height === height ? was : { width, height }));
      }
      frame = requestAnimationFrame(follow);
    };
    follow();
    return () => cancelAnimationFrame(frame);
  }, [step]);

  const close = () => {
    markSeen();
    setAt(null);
  };
  const go = (to: number) => (to >= steps.length ? close() : setAt(Math.max(0, to)));

  useEffect(() => {
    if (at === null) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
      else if (event.key === "ArrowRight") go(at + 1);
      else if (event.key === "ArrowLeft") go(at - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  useEffect(() => {
    card.current?.focus({ preventScroll: true });
  }, [at]);

  // Modal while open: the page behind is inert, so neither Tab nor a tap reaches it, and focus
  // goes back where it was when the tour ends.
  const open = at !== null;
  useEffect(() => {
    if (!open) return;
    const from = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const layer = card.current?.closest<HTMLElement>("[data-slot=tour]");
    const behind = Array.from(document.body.children).filter(
      (element): element is HTMLElement => element instanceof HTMLElement && element !== layer && !element.inert,
    );
    for (const element of behind) element.inert = true;
    return () => {
      for (const element of behind) element.inert = false;
      if (from?.isConnected) from.focus({ preventScroll: true });
    };
  }, [open]);

  if (!step || at === null) return null;
  const words = t.tour.steps[step.key];
  const last = at === steps.length - 1;
  const lit = rect && step.page ? fitAbove(rect, size) : rect;
  const position = cardPosition(lit, size);
  const box = lit ? ring(lit) : null;

  // On the body itself, outside the app's subtree, which a phone's open menu makes inert.
  return createPortal(
    <div data-slot="tour" className="fixed inset-0 z-[70]" role="presentation">
      {/* The page dimmed around the lit place: one box whose shadow is the dim. */}
      <div
        aria-hidden
        className={cn(
          "absolute rounded-2xl transition-all duration-300 ease-[cubic-bezier(0.32,0.72,0,1)] motion-reduce:transition-none",
          // On the black theme a dim alone barely shows: the place is also ringed in the brand's
          // colour, and the dim is deeper.
          "[--tour-dim:55%] dark:[--tour-dim:75%]",
          !lit && "opacity-0",
        )}
        style={{
          top: box ? box.top : innerHeight / 2,
          left: box ? box.left : innerWidth / 2,
          width: box ? box.width : 0,
          height: box ? box.height : 0,
          boxShadow: [
            "0 0 0 2px var(--primary)",
            "0 0 24px 2px color-mix(in oklab, var(--primary) 45%, transparent)",
            "0 0 0 9999px color-mix(in oklab, var(--scrim) var(--tour-dim), transparent)",
          ].join(", "),
        }}
      />
      {/* Without a lit place the dim is a plain layer. */}
      {!lit ? (
        <div aria-hidden className="bg-scrim/55 dark:bg-scrim/75 animate-in fade-in-0 absolute inset-0 duration-200" />
      ) : null}
      {pointer ? (
        // The hand that shows where to tap: an arrow in the page's own ink, a ripple as it taps.
        <div
          aria-hidden
          className="pointer-events-none absolute top-0 left-0 z-10 transition-transform ease-[cubic-bezier(0.32,0.72,0,1)]"
          style={{ transform: `translate(${pointer.x}px, ${pointer.y}px)`, transitionDuration: `${GLIDE}ms` }}
        >
          {pointer.tap ? <span className="bg-primary/50 absolute -top-4 -left-4 size-8 animate-ping rounded-full" /> : null}
          <svg
            viewBox="0 0 24 24"
            className={cn("size-6 drop-shadow-md transition-transform duration-150", pointer.tap && "scale-90")}
          >
            <path
              d="M4 2.5 19.5 13l-6.7 1.3 3.9 7.2-2.8 1.5-3.9-7.3L4 20.5Z"
              className="fill-foreground stroke-background"
              strokeWidth={1.5}
              strokeLinejoin="round"
            />
          </svg>
        </div>
      ) : null}
      <div
        ref={card}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="bg-popover text-popover-foreground ring-foreground/5 absolute grid w-[min(22rem,calc(100vw-2rem))] gap-3 rounded-2xl p-4 shadow-2xl ring-1 outline-none transition-[top,left] duration-300 ease-[cubic-bezier(0.32,0.72,0,1)] motion-reduce:transition-none"
        style={position}
      >
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground text-xs tabular-nums">{t.tour.step(at + 1, steps.length)}</span>
          <span className="ms-auto flex gap-1" aria-hidden>
            {steps.map((one, index) => (
              <span
                key={one.key}
                className={cn("h-1.5 rounded-full transition-all duration-300", index === at ? "bg-primary w-4" : "bg-border w-1.5")}
              />
            ))}
          </span>
        </div>
        <div className="grid gap-1.5">
          <h2 id={titleId} className="text-base font-medium text-balance">
            {words.title}
          </h2>
          <p className="text-muted-foreground text-sm text-pretty">{words.body}</p>
        </div>
        <div className="flex items-center gap-2 pt-1">
          {last ? null : (
            <Button variant="ghost" size="sm" onClick={close}>
              {t.tour.skip}
            </Button>
          )}
          <span className="ms-auto flex gap-2">
            {at > 0 ? (
              <Button variant="outline" size="sm" onClick={() => go(at - 1)}>
                {t.tour.back}
              </Button>
            ) : null}
            <Button size="sm" onClick={() => go(at + 1)}>
              {at === 0 ? t.tour.start : last ? t.tour.done : t.tour.next}
            </Button>
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** The ring around a lit place, kept whole on the screen: a button at its very edge (the
 * playground's bar) is ringed inside the edge rather than cut off by it. */
function ring(lit: DOMRect): { top: number; left: number; width: number; height: number } {
  const top = Math.max(EDGE, lit.top - PAD);
  const left = Math.max(EDGE, lit.left - PAD);
  const bottom = Math.min(innerHeight - EDGE, lit.bottom + PAD);
  const right = Math.min(innerWidth - EDGE, lit.right + PAD);
  return { top, left, width: Math.max(0, right - left), height: Math.max(0, bottom - top) };
}

/** A page lit whole is taller than the screen: lit from its top down to where the card fits
 * under it, as long as a fair part of it stays lit. Its ring stays inside it, so it never rides
 * over the bar above the page. */
function fitAbove(rect: DOMRect, size: { width: number; height: number }): DOMRect {
  const height = size.height || 200;
  const top = Math.max(rect.top + PAD, 16 + PAD);
  const bottom = Math.min(rect.bottom, innerHeight - 16 - height - GAP - PAD);
  if (bottom - top < 120) return rect;
  return new DOMRect(rect.left, top, rect.width, bottom - top);
}

/** Where the card goes: under the lit place when it fits, else over it, else beside it; centred
 * at the foot of the screen with nothing lit. Always inside the screen. */
function cardPosition(rect: DOMRect | null, size: { width: number; height: number }): { top: number; left: number } {
  const width = size.width || Math.min(352, innerWidth - 32);
  const height = size.height || 200;
  const clampX = (x: number) => Math.min(Math.max(16, x), innerWidth - width - 16);
  const clampY = (y: number) => Math.min(Math.max(16, y), innerHeight - height - 16);
  if (!rect) {
    // A phone keeps it at the foot, in reach of the thumb; a desk in the middle.
    const phone = innerWidth < 768;
    return { left: clampX((innerWidth - width) / 2), top: phone ? innerHeight - height - 24 : clampY((innerHeight - height) / 2) };
  }
  const below = rect.bottom + PAD + GAP;
  const above = rect.top - PAD - GAP - height;
  if (below + height <= innerHeight - 16) return { top: below, left: clampX(rect.left) };
  if (above >= 16) return { top: above, left: clampX(rect.left) };
  // A tall place (the chat list): beside it, on the side with room.
  const right = rect.right + PAD + GAP;
  const left = right + width <= innerWidth - 16 ? right : rect.left - PAD - GAP - width;
  return { top: clampY(rect.top), left: clampX(left) };
}
