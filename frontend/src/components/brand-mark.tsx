import { cn } from "@/lib/utils";

/**
 * Tamga's mark: a seal — a rounded square with a stamped T. Mirrors `src/app/icon.svg`.
 * Decorative by default; pass a `title` where it stands alone as a link.
 */
export function BrandMark({ className, title }: { className?: string; title?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      className={cn("size-8", className)}
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
    >
      {title ? <title>{title}</title> : null}
      <rect width="32" height="32" rx="9" className="fill-primary" />
      <path
        d="M9.5 10.5h13M16 10.5v11.5M12.5 22h7"
        className="stroke-primary-foreground"
        strokeWidth="2.4"
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  );
}
