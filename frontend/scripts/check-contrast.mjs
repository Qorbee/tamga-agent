// Fails when a text token in src/app/globals.css drops below WCAG AA against the surfaces it sits on,
// in the light theme and in the black one. Runs inside `pnpm lint`, so a palette change cannot
// quietly make secondary or status text unreadable.
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../src/app/globals.css", import.meta.url), "utf8");
const tokensIn = (block) =>
  Object.fromEntries(
    [...block.matchAll(/--([\w-]+):\s*(#[0-9a-f]{6})\s*;/gi)].map(([, name, hex]) => [name, hex]),
  );
const light = tokensIn(css.match(/:root\s*{([\s\S]*?)\n}/)?.[1] ?? "");
const darkBlock = css.match(/:root\[data-theme="dark"\]\s*{([\s\S]*?)\n}/)?.[1];
if (!darkBlock) {
  // Otherwise the black theme would inherit every light token and pass unchecked.
  console.error('contrast: no :root[data-theme="dark"] block in globals.css');
  process.exit(1);
}
// The black theme overrides some tokens and inherits the rest.
const dark = { ...light, ...tokensIn(darkBlock) };

const TEXT = 4.5; // body-size text
const NON_TEXT = 3; // icons, dots and fills (WCAG 1.4.11)

// [foreground token, background tokens, minimum ratio]
const pairs = [
  ["foreground", ["background", "card", "muted", "accent", "success-soft", "warning-soft", "danger-soft"], TEXT],
  ["muted-foreground", ["background", "card", "muted", "success-soft", "warning-soft", "danger-soft"], TEXT],
  ["primary-foreground", ["primary"], TEXT],
  ["sidebar-foreground", ["sidebar", "sidebar-accent"], TEXT],
  ["success-foreground", ["background", "card", "success-soft"], TEXT],
  ["warning-foreground", ["background", "card", "warning-soft"], TEXT],
  ["danger-foreground", ["background", "card", "danger-soft"], TEXT],
  ["scrim-foreground", ["scrim"], TEXT],
  ["success", ["background", "card"], NON_TEXT],
  ["warning", ["background", "card"], NON_TEXT],
  ["danger", ["background", "card"], NON_TEXT],
  ["ring", ["background", "card"], NON_TEXT],
];
// Terracotta passes as text only on the light surfaces; on charcoal it is used as a fill alone.
const lightOnly = [["primary", ["background", "card"], TEXT]];


function luminance(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

let failed = 0;
let checked = 0;
for (const [theme, tokens, themePairs] of [
  ["light", light, [...pairs, ...lightOnly]],
  ["black", dark, pairs],
]) {
  for (const [fg, bgs, min] of themePairs) {
    for (const bg of bgs) {
      checked++;
      if (!tokens[fg] || !tokens[bg]) {
        console.error(`contrast (${theme}): token --${tokens[fg] ? bg : fg} is missing`);
        failed++;
        continue;
      }
      const r = ratio(tokens[fg], tokens[bg]);
      if (r < min) {
        console.error(`contrast (${theme}): --${fg} on --${bg} is ${r.toFixed(2)}:1, needs ${min}:1`);
        failed++;
      }
    }
  }
}

if (failed) process.exit(1);
console.log(`contrast: ${checked} pairs pass`);
