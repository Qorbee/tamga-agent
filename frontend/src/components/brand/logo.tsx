"use client";

import { useEffect, useId, useRef, useState, useSyncExternalStore } from "react";

import { cn } from "@/lib/utils";

import { FLAT, HEIGHT, WAX, WORD } from "./geometry";

/**
 * The four candidates for Tamga's logo: a seal, flat or of wax, with a T or the steppe glyph cut
 * in it. Which one shows is a device setting while the owner compares them (Settings, General);
 * once one is chosen, the others and the setting go.
 */
export const LOGOS = ["flatT", "flatG", "waxT", "waxG"] as const;
export type Logo = (typeof LOGOS)[number];

const LOGO_COOKIE = "tamga_logo";
const FIRST: Logo = "flatT";

type Shape = {
  finish: "flat" | "wax";
  letter: "T" | "G";
  // The seal alone, as a square; and how wide the lockup is with the word rolled in.
  square: string;
  mark: number;
};

const SHAPES: Record<Logo, Shape> = {
  flatT: { finish: "flat", letter: "T", square: "14 6 180 180", mark: 200 },
  flatG: { finish: "flat", letter: "G", square: "14 6 180 180", mark: 200 },
  waxT: { finish: "wax", letter: "T", square: "6 4 196 196", mark: 204 },
  waxG: { finish: "wax", letter: "G", square: "6 4 196 196", mark: 204 },
};

// The lockup with its word out.
const FULL = 660;
// The word, from the seal's middle to its end and a little past, rolls in this far.
const ROLL = WORD.right - FLAT.cx + 12;

const listeners = new Set<() => void>();

function readLogo(): Logo {
  const value = document.cookie.match(/(?:^|; )tamga_logo=([^;]+)/)?.[1];
  return (LOGOS as readonly string[]).includes(value ?? "") ? (value as Logo) : FIRST;
}

/** The candidate this device shows. The server draws the first, and the browser its own after. */
export function useLogo(): Logo {
  return useSyncExternalStore(
    (changed) => {
      listeners.add(changed);
      return () => listeners.delete(changed);
    },
    readLogo,
    () => FIRST,
  );
}

export function saveLogo(logo: Logo): void {
  document.cookie = `${LOGO_COOKIE}=${logo}; path=/; max-age=31536000; samesite=lax`;
  for (const changed of listeners) changed();
}

/** The seal, in the logo's units. Wax is an image; below icon sizes it hands over to its flat
 * twin, whose letter is cut through so the page shows in it. */
function SealBody({ shape, small }: { shape: Shape; small: boolean }) {
  const cut = useId();
  if (shape.finish === "wax" && !small)
    return <image href={`/brand/wax-${shape.letter.toLowerCase()}.webp`} {...WAX.image} />;
  return (
    <>
      <defs>
        <mask id={cut} maskUnits="userSpaceOnUse" x="0" y="0" width={FULL} height={HEIGHT}>
          <rect width={FULL} height={HEIGHT} fill="#fff" />
          <path d={FLAT.letters[shape.letter]} fill="#000" />
        </mask>
      </defs>
      <path d={FLAT.outline} className="fill-primary" mask={`url(#${cut})`} />
    </>
  );
}

/**
 * Tamga's mark: the seal alone, for an icon, an avatar or a line. `small` draws a wax seal flat,
 * as it is at icon sizes. Decorative: what it stands beside names it.
 */
export function Seal({
  logo,
  className,
  small = false,
}: {
  // One candidate in particular, for choosing between them; else the one this device shows.
  logo?: Logo;
  className?: string;
  small?: boolean;
}) {
  const shown = useLogo();
  const shape = SHAPES[logo ?? shown];
  const square = small && shape.finish === "wax" ? SHAPES.flatT.square : shape.square;
  return (
    <svg
      viewBox={square}
      className={cn("size-8", className)}
      aria-hidden
    >
      <SealBody shape={shape} small={small} />
    </svg>
  );
}

// Once a page load: the seal is stamped and the word written out the first time the lockup shows.
let introduced = false;

function still(): boolean {
  return matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * The seal and "amga" beside it, `height` pixels high. Closed, the word rolls into the seal and
 * the box narrows to the seal; open, it rolls back out. The seal presses once as the word goes
 * in. With `intro`, the first one shown in a page load lands like a stamp before the word
 * comes out. Without motion when the device asks for none.
 */
export function Lockup({
  open,
  height,
  label,
  intro = false,
  className,
}: {
  open: boolean;
  height: number;
  // The product's name, as the page's dictionary gives it.
  label: string;
  intro?: boolean;
  className?: string;
}) {
  const logo = useLogo();
  const shape = SHAPES[logo];
  const behind = useId();
  const seal = useRef<SVGGElement>(null);
  const [landing, setLanding] = useState(() => intro && !introduced);
  const wasOpen = useRef(open);

  useEffect(() => {
    if (!landing) return;
    introduced = true;
    const element = seal.current;
    if (!element || still()) {
      setLanding(false);
      return;
    }
    element.animate(
      [
        { transform: "translateY(-14px) scale(1.3)", opacity: 0, easing: "cubic-bezier(0.7, 0, 0.84, 0)" },
        { transform: "none", opacity: 1, offset: 0.33, easing: "ease-out" },
        { transform: "scale(1.07, 0.9)", offset: 0.42 },
        { transform: "scale(0.97, 1.04)", offset: 0.6 },
        { transform: "scale(1.01, 0.99)", offset: 0.78 },
        { transform: "none" },
      ],
      { duration: 960 },
    );
    // The word comes out as the seal settles.
    const out = setTimeout(() => setLanding(false), 480);
    return () => clearTimeout(out);
  }, [landing]);

  useEffect(() => {
    const closed = wasOpen.current && !open;
    wasOpen.current = open;
    const element = seal.current;
    if (!closed || !element || still()) return;
    // A press as the word reaches it; opened again first, there is no press.
    const press = element.animate(
      [
        { transform: "none" },
        { transform: "scale(1.05, 0.93)", offset: 0.22, easing: "ease-out" },
        { transform: "scale(0.98, 1.03)", offset: 0.5 },
        { transform: "scale(1.01, 0.99)", offset: 0.75 },
        { transform: "none" },
      ],
      { duration: 640, delay: 710 },
    );
    return () => press.cancel();
  }, [open]);

  const out = open && !landing;
  const scale = height / HEIGHT;
  const motion = "duration-[850ms] ease-[cubic-bezier(0.45,0,0.55,1)] motion-reduce:transition-none";
  return (
    <span
      className={cn("block shrink-0 overflow-hidden transition-[width]", motion, className)}
      style={{ width: (out ? FULL : shape.mark) * scale, height }}
    >
      <svg viewBox={`0 0 ${FULL} ${HEIGHT}`} width={FULL * scale} height={height} role="img" aria-label={label}>
        <defs>
          {/* The word passes behind the seal: hidden left of its middle and under its outline. */}
          <mask id={behind} maskUnits="userSpaceOnUse" x="0" y="0" width={FULL} height={HEIGHT}>
            <rect width={FULL} height={HEIGHT} fill="#fff" />
            <rect width={FLAT.cx} height={HEIGHT} fill="#000" />
            <path d={shape.finish === "wax" ? WAX.outline : FLAT.outline} fill="#000" />
          </mask>
        </defs>
        <g mask={`url(#${behind})`}>
          <path
            d={WORD.path}
            className={cn("fill-foreground transition-transform", motion)}
            style={{ transform: out ? "none" : `translateX(${-ROLL}px)` }}
          />
        </g>
        <g
          ref={seal}
          style={{
            transformBox: "view-box",
            transformOrigin: `${FLAT.cx}px ${shape.finish === "wax" ? WAX.cy : FLAT.cy}px`,
            opacity: landing ? 0 : undefined,
          }}
        >
          <SealBody shape={shape} small={false} />
        </g>
      </svg>
    </span>
  );
}
