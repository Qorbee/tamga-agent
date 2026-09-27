import type { Locale } from "@/lib/i18n";

/**
 * The only place money, numbers and dates are formatted.
 *
 * Currency rule (docs/architecture/07-web-app.md): documents keep the currency they were issued in;
 * anything about moving money leads with the stablecoin, with the document currency secondary.
 * Stablecoins are not ISO 4217 codes — `Intl` throws on "USDC" — so they are formatted as a figure
 * followed by the token.
 */
const STABLECOINS = new Set(["USDC", "EURC"]);

const intlLocale: Record<Locale, string> = { en: "en-GB", tr: "tr-TR" };

export function formatMoney(amount: number, currency: string, locale: Locale): string {
  if (STABLECOINS.has(currency)) {
    const figure = new Intl.NumberFormat(intlLocale[locale], {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(amount);
    return `${figure} ${currency}`;
  }
  return new Intl.NumberFormat(intlLocale[locale], { style: "currency", currency }).format(amount);
}

export function formatNumber(value: number, locale: Locale): string {
  return new Intl.NumberFormat(intlLocale[locale]).format(value);
}

export function formatDate(
  date: Date,
  locale: Locale,
  style: "day" | "dateTime" = "day",
): string {
  return new Intl.DateTimeFormat(intlLocale[locale], {
    weekday: "short",
    day: "numeric",
    month: "short",
    ...(style === "dateTime" ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(date);
}
