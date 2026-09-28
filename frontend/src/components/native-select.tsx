import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

/** The platform's own select, styled like `Input`: on a phone it opens the system picker. */
export function NativeSelect({ className, ...props }: ComponentProps<"select">) {
  return (
    <select
      className={cn(
        "border-input bg-input/30 focus-visible:border-ring focus-visible:ring-ring/50 h-9 w-full min-w-0 rounded-4xl border px-3 text-base outline-none focus-visible:ring-[3px] disabled:opacity-50 md:text-sm",
        className,
      )}
      {...props}
    />
  );
}
