/**
 * Excel's number format codes, applied as Excel applies them: `#,##0.00` shows 1234.5 as
 * `1.234,50` for a Turkish reader and `1,234.50` for an English one, `0%` shows 0.25 as `25%`,
 * `dd.mm.yyyy` a date. Separators, month and day names come from the reader's locale, as Excel takes
 * them from the computer's. Called through `formatSheetCell` in `format.ts`.
 *
 * Covered: sections (positive;negative;zero;text), digit placeholders (0 # ?), grouping and
 * scaling commas, percent, scientific notation, quoted and escaped literals, `_x` spacing, `*x`
 * fill, currency tags (`[$€-2]`), colour tags (dropped), dates and times with elapsed hours, and the
 * system date formats. A fraction (`# ?/?`) falls back to General. Conditions (`[<100]`) are not
 * weighed: a section is picked by the value's sign alone.
 */

type Token =
  | { kind: "literal"; text: string }
  | { kind: "digits"; pattern: string }
  | { kind: "percent" }
  // An unquoted "/": in a date, the reader's date separator (a point in Turkish), as Excel shows it.
  | { kind: "slash" }
  | { kind: "date"; code: string }
  // "@": the text of a text cell.
  | { kind: "text" };

// Excel's day zero; a date is the number of days since.
const EPOCH = Date.UTC(1899, 11, 30);
const DAY = 86_400_000;
const DATE_LETTERS = /[ymdhs]/i;
// Excel's built-in short date (format 14) and the system's own formats follow the reader's settings.
const SHORT_DATE = new Set(["mm-dd-yy", "m/d/yy"]);

export function formatExcelNumber(value: number, code: string, locale: string): string {
  const system = systemDate(code, locale, fromSerial(value));
  if (system !== null) return system;
  const [section, sign] = pickSection(numberSections(code), value);
  const tokens = tokenize(section);
  if (tokens.some((token) => token.kind === "date")) return formatDateTokens(tokens, fromSerial(value), locale, value);
  if (tokens.some((token) => token.kind === "slash") && tokens.some((token) => token.kind === "digits")) {
    return general(value, locale);
  }
  return formatNumberTokens(tokens, Math.abs(value), sign, locale);
}

/** A text cell through its code's text section, `@` standing for the text: `"ID: "@` shows `ID:
 *  ABC`, and accounting's `_-@_-` pads it. A code without a text section shows the text as it is. */
export function formatExcelText(text: string, code: string): string {
  const section = textSection(splitSections(code));
  if (section === null) return text;
  return tokenize(section)
    .map((token) => (token.kind === "literal" ? token.text : token.kind === "text" ? text : ""))
    .join("");
}

/** The fourth section, or a last one holding `@`. */
function textSection(sections: string[]): string | null {
  if (sections.length >= 4) return sections[3]!;
  const last = sections[sections.length - 1]!;
  return tokenize(last).some((token) => token.kind === "text") ? last : null;
}

/** The sections a number is shown with: a text section in last place is not one of them. */
function numberSections(code: string): string[] {
  const sections = splitSections(code);
  return sections.length < 4 && textSection(sections) !== null ? sections.slice(0, -1) : sections;
}

export function formatExcelDate(iso: string, code: string, locale: string): string {
  const moment = parseIso(iso);
  if (!moment) return iso;
  const system = systemDate(code, locale, moment);
  if (system !== null) return system;
  const tokens = tokenize(splitSections(code)[0] ?? "General");
  if (!tokens.some((token) => token.kind === "date")) {
    return new Intl.DateTimeFormat(locale, { dateStyle: "short", timeStyle: "short", timeZone: "UTC" }).format(moment);
  }
  return formatDateTokens(tokens, moment, locale, (moment.getTime() - EPOCH) / DAY);
}

function splitSections(code: string): string[] {
  const sections: string[] = [];
  let current = "";
  let quoted = false;
  let bracket = false;
  for (let index = 0; index < code.length; index++) {
    const char = code[index]!;
    if (char === "\\" && !quoted) {
      current += char + (code[index + 1] ?? "");
      index++;
      continue;
    }
    if (char === '"') quoted = !quoted;
    else if (char === "[" && !quoted) bracket = true;
    else if (char === "]" && !quoted) bracket = false;
    if (char === ";" && !quoted && !bracket) {
      sections.push(current);
      current = "";
    } else current += char;
  }
  sections.push(current);
  return sections;
}

/** The section a value is shown with, and whether a minus goes in front (the negative section
 *  writes its own sign, in brackets or otherwise). A section left empty hides its values: `0;;`
 *  shows neither negatives nor zero. A section left out falls back to the first. */
function pickSection(sections: string[], value: number): [string, string] {
  const [positive = "General", negative, zero] = sections;
  if (value < 0) return negative !== undefined ? [negative, ""] : [positive, "-"];
  if (value === 0 && zero !== undefined) return [zero, ""];
  return [positive, ""];
}

function tokenize(section: string): Token[] {
  const tokens: Token[] = [];
  const literal = (text: string) => {
    const last = tokens[tokens.length - 1];
    if (last?.kind === "literal") last.text += text;
    else tokens.push({ kind: "literal", text });
  };
  for (let index = 0; index < section.length; index++) {
    const char = section[index]!;
    const rest = section.slice(index);
    if (/^general/i.test(rest)) {
      tokens.push({ kind: "digits", pattern: "General" });
      index += "general".length - 1;
    } else if (char === '"') {
      const end = section.indexOf('"', index + 1);
      literal(section.slice(index + 1, end === -1 ? undefined : end));
      index = end === -1 ? section.length : end;
    } else if (char === "\\") {
      literal(section[index + 1] ?? "");
      index++;
    } else if (char === "_") {
      // Room as wide as the next character, which lines up columns of figures.
      literal(" ");
      index++;
    } else if (char === "*") {
      index++; // repeat to fill the cell: a column's width is not the figure's
    } else if (char === "[") {
      const end = section.indexOf("]", index);
      const tag = section.slice(index + 1, end === -1 ? undefined : end);
      index = end === -1 ? section.length : end;
      if (/^(h+|m+|s+)$/i.test(tag)) tokens.push({ kind: "date", code: `[${tag.toLowerCase()}]` });
      else if (tag.startsWith("$")) literal(tag.slice(1).split("-")[0] ?? "");
    } else if (char === "%") {
      tokens.push({ kind: "percent" });
    } else if (char === "/") {
      tokens.push({ kind: "slash" });
    } else if (char === "@") {
      tokens.push({ kind: "text" });
    } else if (/^(AM\/PM|A\/P)/i.test(rest)) {
      const code = /^AM\/PM/i.test(rest) ? "AM/PM" : "A/P";
      tokens.push({ kind: "date", code });
      index += code.length - 1;
    } else if (DATE_LETTERS.test(char)) {
      let run = char;
      while (section[index + run.length]?.toLowerCase() === char.toLowerCase()) run += section[index + run.length];
      tokens.push({ kind: "date", code: run.toLowerCase() });
      index += run.length - 1;
    } else if (/[0#?.,]/.test(char) || (/[eE]/.test(char) && /[+-]/.test(section[index + 1] ?? ""))) {
      let run = "";
      let at = index;
      while (at < section.length) {
        const next = section[at]!;
        if (/[0#?.,]/.test(next)) run += next;
        else if (/[eE]/.test(next) && /[+-]/.test(section[at + 1] ?? "")) {
          run += next + section[at + 1];
          at++;
        } else break;
        at++;
      }
      const last = tokens[tokens.length - 1];
      // A comma or point between literals ("Tel. 0") is text, not a figure.
      if (!/[0#?]/.test(run)) literal(run);
      else if (last?.kind === "digits") last.pattern += run;
      else tokens.push({ kind: "digits", pattern: run });
      index = at - 1;
    } else literal(char);
  }
  return tokens;
}

function formatNumberTokens(tokens: Token[], value: number, sign: string, locale: string): string {
  const percents = tokens.filter((token) => token.kind === "percent").length;
  const scaled = value * 100 ** percents;
  const runs = tokens.flatMap((token) => (token.kind === "digits" ? [token.pattern] : []));
  const figures = runs[0] === "General" ? [general(scaled, locale)] : spread(scaled, runs, locale);
  let out = "";
  let run = 0;
  for (const token of tokens) {
    if (token.kind === "literal") out += token.text;
    else if (token.kind === "percent") out += "%";
    else if (token.kind === "slash") out += "/";
    else if (token.kind === "digits") out += figures[run++] ?? "";
  }
  // A code with no figure in it ("-" for zero) shows only its text. Spaces are kept: `_)` leaves
  // room for the bracket a negative figure closes with, so the digits line up.
  return (sign && runs.length > 0 ? sign : "") + out;
}

/**
 * The figure written across every run of placeholders in a code, as `000-00-0000` writes 123456789
 * as `123-45-6789`: the whole part fills the runs from the right, digits past them going into the
 * first, and the fraction fills them from the left.
 */
function spread(value: number, runs: string[], locale: string): string[] {
  const joined = runs.join("");
  if (runs.length === 1 || /[eE][+-]/.test(joined)) return [digits(value, joined, locale)];
  const point = decimalPoint(locale);
  const text = digits(value, joined, locale);
  const [whole = "", fraction] = text.split(point);
  const pointAt = runs.findIndex((pattern) => pattern.includes("."));
  const slots = (part: string | undefined) => (part?.match(/[0#?]/g) ?? []).length;
  const wholeSlots = runs.map((pattern, index) =>
    pointAt !== -1 && index > pointAt ? 0 : slots(index === pointAt ? pattern.split(".")[0] : pattern),
  );
  const fractionSlots = runs.map((pattern, index) =>
    pointAt === -1 || index < pointAt ? 0 : slots(index === pointAt ? pattern.split(".")[1] : pattern),
  );
  const parts = runs.map(() => "");
  const wholeRuns = runs.flatMap((_, index) => (wholeSlots[index]! > 0 ? [index] : []));
  if (wholeRuns.length === 0) wholeRuns.push(Math.max(pointAt, 0));
  let rest = whole;
  for (let at = wholeRuns.length - 1; at >= 0; at--) {
    const index = wholeRuns[at]!;
    const take = at === 0 ? rest.length : Math.min(wholeSlots[index]!, rest.length);
    parts[index] = rest.slice(rest.length - take);
    rest = rest.slice(0, rest.length - take);
  }
  if (fraction !== undefined && pointAt !== -1) {
    parts[pointAt] += point;
    const fractionRuns = runs.flatMap((_, index) => (fractionSlots[index]! > 0 ? [index] : []));
    let left = fraction;
    fractionRuns.forEach((index, at) => {
      const take = at === fractionRuns.length - 1 ? left.length : Math.min(fractionSlots[index]!, left.length);
      parts[index] += left.slice(0, take);
      left = left.slice(take);
    });
  }
  return parts;
}

function digits(value: number, pattern: string, locale: string): string {
  const exponent = /[eE][+-]/.exec(pattern);
  if (exponent) return scientific(value, pattern.slice(0, exponent.index), pattern.slice(exponent.index + 2), locale);
  const [whole = "", fraction = ""] = pattern.split(".");
  // Commas after the last placeholder divide by a thousand each: "#,##0," shows thousands, and
  // "0.0,," millions.
  const trailing = (part: string) => /,+$/.exec(part)?.[0].length ?? 0;
  const scaling = trailing(whole) + trailing(fraction);
  const integer = whole.slice(0, whole.length - trailing(whole));
  const minimumIntegerDigits = (integer.match(/0/g) ?? []).length;
  const minimumFractionDigits = (fraction.match(/0/g) ?? []).length;
  const maximumFractionDigits = (fraction.match(/[0#?]/g) ?? []).length;
  const text = new Intl.NumberFormat(locale, {
    minimumIntegerDigits: Math.max(1, Math.min(minimumIntegerDigits, 21)),
    minimumFractionDigits: Math.min(minimumFractionDigits, 20),
    maximumFractionDigits: Math.min(Math.max(minimumFractionDigits, maximumFractionDigits), 20),
    useGrouping: integer.includes(","),
  }).format(value / 1000 ** scaling);
  // "#" shows no digit for a zero whole part: "#.##" writes 0.5 as ".5", and "#" writes 0 as "".
  if (minimumIntegerDigits === 0 && Math.abs(value / 1000 ** scaling) < 1) return text.replace(/^0/, "");
  return text;
}

function scientific(value: number, mantissa: string, power: string, locale: string): string {
  const fraction = mantissa.split(".")[1] ?? "";
  const places = (fraction.match(/[0#?]/g) ?? []).length;
  const [base = "0", raw = "0"] = value.toExponential(places).split("e");
  const exponent = Number(raw);
  const number = new Intl.NumberFormat(locale, {
    minimumFractionDigits: (fraction.match(/0/g) ?? []).length,
    maximumFractionDigits: places,
  }).format(Number(base));
  const width = (power.match(/0/g) ?? []).length;
  return `${number}E${exponent < 0 ? "-" : "+"}${String(Math.abs(exponent)).padStart(width, "0")}`;
}

/** Excel's General: up to eleven characters, in scientific notation beyond them. */
function general(value: number, locale: string): string {
  const size = Math.abs(value);
  if (size !== 0 && (size >= 1e11 || size < 1e-9)) return scientific(value, "0.00000", "00", locale);
  const whole = size < 1 ? 1 : Math.floor(Math.log10(size)) + 1;
  return new Intl.NumberFormat(locale, { maximumFractionDigits: Math.max(0, 10 - whole), useGrouping: false }).format(value);
}

function formatDateTokens(tokens: Token[], moment: Date, locale: string, serial: number): string {
  const twelve = tokens.some((token) => token.kind === "date" && (token.code === "AM/PM" || token.code === "A/P"));
  const hours = moment.getUTCHours();
  const name = (options: Intl.DateTimeFormatOptions) =>
    new Intl.DateTimeFormat(locale, { ...options, timeZone: "UTC" }).format(moment);
  const pad = (value: number, width: number) => String(value).padStart(width, "0");
  const dated = tokens.flatMap((token, index) => (token.kind === "date" ? [{ index, code: token.code }] : []));
  const separator = dateSeparator(locale);
  let out = "";
  tokens.forEach((token, index) => {
    if (token.kind === "literal") out += token.text;
    else if (token.kind === "percent") out += "%";
    else if (token.kind === "slash") out += separator;
    else if (token.kind === "digits") out += token.pattern.replace(/[#?]/g, "");
    else if (token.kind === "date") {
      const code = token.code;
      // "m" is a month, except right after an hour or right before a second, where it is minutes.
      const position = dated.findIndex((entry) => entry.index === index);
      const before = dated[position - 1]?.code.replace("[", "");
      const after = dated[position + 1]?.code.replace("[", "");
      const minutes = code.startsWith("m") && code.length <= 2 && (before?.startsWith("h") || after?.startsWith("s"));
      if (code === "[h]" || code === "[hh]") out += pad(Math.floor(serial * 24), code.length - 2);
      else if (code === "[m]" || code === "[mm]") out += pad(Math.floor(serial * 1440), code.length - 2);
      else if (code === "[s]" || code === "[ss]") out += pad(Math.floor(serial * 86400), code.length - 2);
      else if (minutes) out += pad(moment.getUTCMinutes(), code.length);
      else if (code.startsWith("y")) out += code.length <= 2 ? pad(moment.getUTCFullYear() % 100, 2) : String(moment.getUTCFullYear());
      else if (code === "m" || code === "mm") out += pad(moment.getUTCMonth() + 1, code.length);
      else if (code === "mmm") out += name({ month: "short" });
      else if (code === "mmmmm") out += name({ month: "long" }).charAt(0);
      else if (code.startsWith("m")) out += name({ month: "long" });
      else if (code === "d" || code === "dd") out += pad(moment.getUTCDate(), code.length);
      else if (code === "ddd") out += name({ weekday: "short" });
      else if (code.startsWith("d")) out += name({ weekday: "long" });
      else if (code.startsWith("h")) out += pad(twelve ? hours % 12 || 12 : hours, Math.min(code.length, 2));
      else if (code.startsWith("s")) out += pad(moment.getUTCSeconds(), Math.min(code.length, 2));
      else if (code === "AM/PM") out += hours < 12 ? "AM" : "PM";
      else if (code === "A/P") out += hours < 12 ? "A" : "P";
    }
  });
  return out;
}

/** The formats Excel leaves to the computer's settings: its built-in short date, and the system
 *  long date and time (`[$-F800]`, `[$-F400]`). */
function systemDate(code: string, locale: string, moment: Date): string | null {
  const format = (options: Intl.DateTimeFormatOptions) =>
    new Intl.DateTimeFormat(locale, { ...options, timeZone: "UTC" }).format(moment);
  if (SHORT_DATE.has(code.toLowerCase())) return format({ day: "2-digit", month: "2-digit", year: "numeric" });
  if (code.toUpperCase().includes("[$-F800]")) return format({ dateStyle: "full" });
  if (code.toUpperCase().includes("[$-F400]")) return format({ timeStyle: "medium" });
  return null;
}

function decimalPoint(locale: string): string {
  return new Intl.NumberFormat(locale).formatToParts(1.5).find((part) => part.type === "decimal")?.value ?? ".";
}

function dateSeparator(locale: string): string {
  const parts = new Intl.DateTimeFormat(locale, { day: "2-digit", month: "2-digit", year: "numeric" }).formatToParts(0);
  return parts.find((part) => part.type === "literal")?.value ?? "/";
}

/** A date from its day number, to the nearest second as Excel rounds it: 14:29:59.9 shows 14:30. */
function fromSerial(serial: number): Date {
  return new Date(EPOCH + Math.round(serial * 86_400) * 1000);
}

/** An ISO date without a zone, as the backend sends one, read as the wall-clock time it names. */
function parseIso(iso: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2})(?::(\d{2}))?)?/.exec(iso);
  if (!match) return null;
  const [, year, month, day, hour = "0", minute = "0", second = "0"] = match;
  return new Date(Date.UTC(+year!, +month! - 1, +day!, +hour, +minute, +second));
}
