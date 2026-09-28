import { ApiError } from "@/lib/api";
import type { Dictionary } from "@/lib/i18n";

/** A sign-in or invite failure in the reader's language, from the backend's stable error code. */
export function authError(error: unknown, t: Dictionary): string {
  if (!(error instanceof ApiError)) return t.auth.errors.offline;
  const known = t.auth.errors as Record<string, string>;
  return known[error.code] ?? t.auth.errors.generic;
}
