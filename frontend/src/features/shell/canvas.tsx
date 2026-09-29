"use client";

import { Cancel01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useRouter } from "next/navigation";
import { useState, type CSSProperties, type ReactNode } from "react";

import { ResizeHandle } from "@/components/resize-handle";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n/client";
import { CANVAS_SIZE, saveSize } from "@/lib/panel-sizes";
import { cn } from "@/lib/utils";

/**
 * Where the `@canvas` slot renders: a card floating beside the chat, its left edge dragged to
 * resize (well past the minimum, it closes); full screen below the `lg` breakpoint. It shows only
 * while a canvas route renders a `CanvasPanel`: the slot's default renders nothing, and so does the
 * frame.
 */
export function CanvasFrame({ initialWidth, children }: { initialWidth: number; children: ReactNode }) {
  const { t } = useI18n();
  const router = useRouter();
  const [width, setWidth] = useState(initialWidth);

  return (
    <aside
      style={{ "--canvas-width": `${width}px` } as CSSProperties}
      className={cn(
        "relative hidden shrink-0 flex-col has-data-[slot=canvas-panel]:flex",
        "max-lg:bg-card max-lg:fixed max-lg:inset-0 max-lg:z-40",
        "lg:bg-card lg:border-border lg:m-2 lg:ms-0 lg:w-(--canvas-width) lg:max-w-[55vw] lg:rounded-2xl lg:border lg:shadow-lg",
      )}
    >
      <ResizeHandle
        label={t.shell.resizeCanvas}
        size={CANVAS_SIZE}
        width={width}
        edge="left"
        onResize={setWidth}
        onResizeEnd={(settled) => saveSize(CANVAS_SIZE, settled)}
        // Dragged well past its minimum, the canvas closes, as its close button does.
        onCollapse={() => router.back()}
        className="max-lg:hidden"
      />
      {children}
    </aside>
  );
}

/**
 * A record open in the canvas: its title, its actions (a download), a close button and the record,
 * which scrolls on its own. A canvas route (an intercepting route under `@canvas`) renders one;
 * closing goes back to the page the owner opened it from. `title` can load with the record, so the
 * panel is named by `label` until it has.
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
  const router = useRouter();

  return (
    <section
      data-slot="canvas-panel"
      aria-label={label}
      className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[inherit]"
    >
      <header className="border-border flex h-12 shrink-0 items-center gap-1 border-b ps-4 pe-2">
        <h2 className="flex min-w-0 flex-1 items-baseline gap-1.5 text-sm font-medium">{title ?? label}</h2>
        {actions}
        <Button variant="ghost" size="icon-sm" onClick={() => router.back()}>
          <HugeiconsIcon icon={Cancel01Icon} strokeWidth={1.75} />
          <span className="sr-only">{t.shell.closeCanvas}</span>
        </Button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
    </section>
  );
}
