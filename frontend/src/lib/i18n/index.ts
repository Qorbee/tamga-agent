import { en, type Dictionary } from "./en";
import { tr } from "./tr";

export type { Dictionary } from "./en";

export const locales = ["en", "tr"] as const;
export type Locale = (typeof locales)[number];
export const defaultLocale: Locale = "en";

/** Each language by its own name, the same in every dictionary: a reader finds their language in it. */
export const localeNames: Record<Locale, string> = { en: "English", tr: "Türkçe" };

/** Set when the user picks a language; the signed-in user's saved setting will take precedence. */
export const LOCALE_COOKIE = "tamga_locale";

const dictionaries: Record<Locale, Dictionary> = { en, tr };

export function getDictionary(locale: Locale): Dictionary {
  return dictionaries[locale];
}

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (locales as readonly string[]).includes(value);
}

/** The cookie wins; otherwise the first supported language in `Accept-Language`; otherwise English. */
export function matchLocale(cookie: string | undefined, acceptLanguage: string | null): Locale {
  if (isLocale(cookie)) return cookie;
  for (const part of acceptLanguage?.split(",") ?? []) {
    const base = part.split(";")[0]?.trim().toLowerCase().split("-")[0];
    if (isLocale(base)) return base;
  }
  return defaultLocale;
}
