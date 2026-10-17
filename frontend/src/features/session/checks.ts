import type { Dictionary } from "@/lib/i18n";

// A name, an @ and a domain with a dot: enough to catch a slip, never a full address grammar.
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** What is wrong with an email as typed, in the reader's language; null when it may be one. */
export function checkEmail(email: string, t: Dictionary): string | null {
  if (!email.trim()) return t.auth.check.emailMissing;
  return EMAIL.test(email.trim()) ? null : t.auth.check.emailInvalid;
}

/** What is wrong with a password as typed; `least` only for a new one. */
export function checkPassword(password: string, t: Dictionary, least = 0): string | null {
  if (!password) return t.auth.check.passwordMissing;
  return password.length < least ? t.auth.check.passwordShort(least) : null;
}
