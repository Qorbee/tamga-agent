import type { ReactNode } from "react";

/** A labelled form control, with an optional hint or error under it. The control names the
 * error with `aria-describedby={fieldErrorId(htmlFor)}`, so a screen reader reads it. */
export function Field({
  label,
  htmlFor,
  hint,
  error,
  children,
}: {
  label: string;
  htmlFor: string;
  hint?: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className="grid gap-1.5">
      <label htmlFor={htmlFor} className="text-sm font-medium">
        {label}
      </label>
      {children}
      {error ? (
        <p id={fieldErrorId(htmlFor)} className="text-danger-foreground text-xs" role="alert">
          {error}
        </p>
      ) : hint ? (
        <p className="text-muted-foreground text-xs">{hint}</p>
      ) : null}
    </div>
  );
}

export const fieldErrorId = (htmlFor: string) => `${htmlFor}-error`;
