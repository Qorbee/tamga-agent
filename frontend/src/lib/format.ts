import type { Sheet } from "@/lib/api";
import { formatExcelDate, formatExcelNumber, formatExcelText } from "@/lib/excel-format";
import type { Locale } from "@/lib/i18n";

/**
 * The only place money, numbers and dates are formatted.
 *
 * Currency rule (ADR-0027, point 8): every amount keeps its own currency, a bill's first; how it is
 * paid follows, in test USDC only for the vault. A currency is written by its narrow sign (₺, $,
 * €, £) where no other currency shares it, the dollar being the US one; any other by the sign that
 * tells it apart ("CA$", "A$"), never a bare "$". A figure converted into another currency says so.
 * Stablecoins are not ISO 4217 codes — `Intl` throws on "USDC" — so they are formatted as a figure
 * followed by the token.
 */
const STABLECOINS = new Set(["USDC", "EURC"]);

const intlLocale: Record<Locale, string> = { en: "en-GB", tr: "tr-TR" };

// The currencies whose narrow sign is theirs alone.
const NARROW = new Set(["TRY", "EUR", "USD", "GBP"]);
const signOf = (currency: string): "narrowSymbol" | "symbol" => (NARROW.has(currency) ? "narrowSymbol" : "symbol");

export function formatMoney(amount: number, currency: string, locale: Locale): string {
  if (STABLECOINS.has(currency)) {
    const figure = new Intl.NumberFormat(intlLocale[locale], {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(amount);
    return `${figure} ${currency}`;
  }
  return new Intl.NumberFormat(intlLocale[locale], { style: "currency", currency, currencyDisplay: signOf(currency) }).format(
    amount,
  );
}

/** A figure for a tile or a bar's legend: whole units, the currency kept. */
export function formatMoneyShort(amount: number, currency: string, locale: Locale): string {
  if (STABLECOINS.has(currency)) {
    return `${new Intl.NumberFormat(intlLocale[locale], { maximumFractionDigits: 0 }).format(amount)} ${currency}`;
  }
  return new Intl.NumberFormat(intlLocale[locale], {
    style: "currency",
    currency,
    currencyDisplay: signOf(currency),
    maximumFractionDigits: 0,
  }).format(amount);
}

/** A figure on a chart: "€6.5K", the currency as its sign, where the chart already says which. */
/** A figure written short ("€1.9K"): with the currency's narrow sign ("$") where something beside it
 * names the currency (a chart's caption), else with its own sign ("US$"), which tells one dollar
 * from another. */
export function formatMoneyCompact(
  amount: number,
  currency: string,
  locale: Locale,
  display: "narrowSymbol" | "symbol" = "narrowSymbol",
): string {
  if (STABLECOINS.has(currency)) {
    return `${new Intl.NumberFormat(intlLocale[locale], { notation: "compact", maximumFractionDigits: 1 }).format(amount)} ${currency}`;
  }
  return new Intl.NumberFormat(intlLocale[locale], {
    style: "currency",
    currency,
    currencyDisplay: display === "narrowSymbol" ? signOf(currency) : display,
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(amount);
}

/** A currency's sign ("€", "₺", "$"), or its code where it has none; `symbol` gives the one that
 * tells it from others ("US$"). */
export function currencySign(currency: string, locale: Locale, display: "narrowSymbol" | "symbol" = "narrowSymbol"): string {
  if (STABLECOINS.has(currency)) return currency;
  const parts = new Intl.NumberFormat(intlLocale[locale], {
    style: "currency",
    currency,
    currencyDisplay: display === "narrowSymbol" ? signOf(currency) : display,
  }).formatToParts(0);
  return parts.find((part) => part.type === "currency")?.value ?? currency;
}

/** USDC from its smallest units (six decimals), every decimal a mirrored bill has kept. */
export function formatUsdc(units: number, locale: Locale): string {
  const figure = new Intl.NumberFormat(intlLocale[locale], {
    minimumFractionDigits: 2,
    maximumFractionDigits: 6,
  }).format(units / 1_000_000);
  return `${figure} USDC`;
}

export function formatNumber(value: number, locale: Locale): string {
  return new Intl.NumberFormat(intlLocale[locale]).format(value);
}

export function formatDate(
  date: Date,
  locale: Locale,
  style: "day" | "dateTime" | "time" = "day",
): string {
  // The hour alone, as a chat shows a message sent today.
  if (style === "time") return new Intl.DateTimeFormat(intlLocale[locale], { hour: "2-digit", minute: "2-digit" }).format(date);
  return new Intl.DateTimeFormat(intlLocale[locale], {
    weekday: "short",
    day: "numeric",
    month: "short",
    ...(style === "dateTime" ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(date);
}

/** A calendar day the API gives as `2026-08-10`, at local midnight: never shifted by a time zone. */
function localDay(day: string): Date {
  const [year = 1970, month = 1, date = 1] = day.split("-").map(Number);
  return new Date(year, month - 1, date);
}

/** Today here, as the API writes a calendar day (`2026-08-10`). */
export function todayIso(now: Date = new Date()): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** A calendar's month heading, "October 2026". */
export function formatMonth(year: number, month: number, locale: Locale): string {
  return new Intl.DateTimeFormat(intlLocale[locale], { month: "long", year: "numeric" }).format(new Date(year, month, 1));
}

/** The year's months by their short names, January first: "Jan", "Feb", … */
export function monthNames(locale: Locale): string[] {
  const format = new Intl.DateTimeFormat(intlLocale[locale], { month: "short" });
  return Array.from({ length: 12 }, (_, month) => format.format(new Date(2026, month, 1)));
}

/** The week's days as a calendar heads its columns, Monday first: "Mo", "Tu", … */
export function weekdayNames(locale: Locale): string[] {
  const format = new Intl.DateTimeFormat(intlLocale[locale], { weekday: "short" });
  // 5 January 2026 was a Monday.
  return Array.from({ length: 7 }, (_, at) => format.format(new Date(2026, 0, 5 + at)).slice(0, 2));
}

/** A whole day as a calendar names it to a screen reader, "Friday 16 October 2026". */
export function formatDayLong(day: string, locale: Locale): string {
  return new Intl.DateTimeFormat(intlLocale[locale], { weekday: "long", day: "numeric", month: "long", year: "numeric" }).format(
    localDay(day),
  );
}

/** A job's days: one day, or the span from the first to the last ("10 – 14 Aug 2026"). */
/** A day as a chart's axis names it, "13 Oct": the year and the weekday left to the readout. */
export function formatDayShort(day: string, locale: Locale): string {
  return new Intl.DateTimeFormat(intlLocale[locale], { day: "numeric", month: "short" }).format(new Date(`${day}T12:00:00`));
}

export function formatDays(start: string | null, end: string | null, locale: Locale): string {
  const format = new Intl.DateTimeFormat(intlLocale[locale], { day: "numeric", month: "short", year: "numeric" });
  if (start && end && start !== end) return format.formatRange(localDay(start), localDay(end));
  const only = start ?? end;
  return only ? format.format(localDay(only)) : "";
}

/** A list row's time: the hour today, the day this year, the full date before. */
export function formatListDate(date: Date, locale: Locale, now: Date = new Date()): string {
  const sameYear = date.getFullYear() === now.getFullYear();
  const sameDay = sameYear && date.getMonth() === now.getMonth() && date.getDate() === now.getDate();
  return new Intl.DateTimeFormat(
    intlLocale[locale],
    sameDay
      ? { hour: "2-digit", minute: "2-digit" }
      : { day: "numeric", month: "short", ...(sameYear ? {} : { year: "numeric" }) },
  ).format(date);
}

/** How long ago a moment was, as "16 hours ago"; null under a minute, which the caller says its way. */
export function formatAgo(date: Date, locale: Locale, now: Date = new Date()): string | null {
  const seconds = (date.getTime() - now.getTime()) / 1000;
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ["year", 31_536_000],
    ["month", 2_592_000],
    ["week", 604_800],
    ["day", 86_400],
    ["hour", 3_600],
    ["minute", 60],
  ];
  const format = new Intl.RelativeTimeFormat(intlLocale[locale], { numeric: "auto" });
  for (const [unit, size] of units) if (Math.abs(seconds) >= size) return format.format(Math.round(seconds / size), unit);
  return null;
}

/** A file's size in decimal units, as file managers show it. */
export function formatBytes(bytes: number, locale: Locale): string {
  const units = ["B", "kB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000;
    unit += 1;
  }
  const digits = unit > 0 && value < 10 ? 1 : 0;
  const figure = new Intl.NumberFormat(intlLocale[locale], { maximumFractionDigits: digits }).format(value);
  return `${figure} ${units[unit]}`;
}

type SheetCell = NonNullable<Sheet["rows"][number]["cells"][number]>;

/** A spreadsheet cell as Excel shows it to a reader in this language (`lib/excel-format.ts`). */
export function formatSheetCell(cell: SheetCell, locale: Locale): string {
  if (cell.kind === "number" && typeof cell.value === "number") {
    return formatExcelNumber(cell.value, cell.format, intlLocale[locale]);
  }
  if (cell.kind === "date" && typeof cell.value === "string") {
    return formatExcelDate(cell.value, cell.format, intlLocale[locale]);
  }
  if (cell.kind === "text" && typeof cell.value === "string") return formatExcelText(cell.value, cell.format);
  return String(cell.value);
}
