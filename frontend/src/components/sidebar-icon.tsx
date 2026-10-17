import { cn } from "@/lib/utils";

/**
 * The sidebar's button: a window with its side column. Open, the column is shaded, and a tap
 * folds it; folded, the window is empty, and a tap opens it. Drawn on the icons' 24-unit grid
 * at their stroke, so it sits beside Hugeicons.
 */
export function SidebarIcon({ open, className }: { open: boolean; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={cn("size-5", className)} aria-hidden>
      {open ? <path d="M7 4.25h2.25v15.5H7a3.75 3.75 0 0 1-3.75-3.75V8A3.75 3.75 0 0 1 7 4.25Z" fill="currentColor" opacity={0.28} /> : null}
      <rect x="3.25" y="4.25" width="17.5" height="15.5" rx="3.75" stroke="currentColor" strokeWidth={1.5} />
      <path d="M9.25 4.25v15.5" stroke="currentColor" strokeWidth={1.5} />
    </svg>
  );
}
