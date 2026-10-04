import type { Sheet } from "@/lib/api";
import { formatExcelDate, formatExcelNumber, formatExcelText } from "@/lib/excel-format";
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

/** A calendar day the API gives as `2026-08-10`, at local midnight: never shifted by a time zone. */
function localDay(day: string): Date {
  const [year = 1970, month = 1, date = 1] = day.split("-").map(Number);
  return new Date(year, month - 1, date);
}

/** Whether a calendar day the API gives is before today, here. */
export function isBeforeToday(day: string, now: Date = new Date()): boolean {
  return localDay(day) < new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

/** A job's days: one day, or the span from the first to the last ("10 – 14 Aug 2026"). */
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
