import { Alert02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";

/** Why a sign-in or an invite was refused, over the button that sent it. */
export function FormAlert({ children }: { children: string }) {
  return (
    <p
      role="alert"
      className="bg-danger-soft text-danger-foreground animate-in fade-in-0 slide-in-from-top-1 flex items-start gap-2 rounded-xl px-3 py-2.5 text-sm duration-150"
    >
      <HugeiconsIcon icon={Alert02Icon} strokeWidth={1.75} className="mt-0.5 size-4 shrink-0" aria-hidden />
      <span className="text-pretty">{children}</span>
    </p>
  );
}
