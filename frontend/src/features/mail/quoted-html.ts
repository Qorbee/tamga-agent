/**
 * An HTML reply split where the mails it quotes begin, its images placed as `parseSafeHtml` places
 * them. Mail clients mark the quote in their markup: Gmail's `div.gmail_quote`, Apple Mail's and
 * Thunderbird's `blockquote type=cite` (after Thunderbird's `div.moz-cite-prefix` line), Outlook on
 * the web's `#divRplyFwdMsg` and `#appendonsend`, Yahoo's `div.yahoo_quoted`. Outlook for Windows marks
 * nothing, so its "From: … Sent: …" block is found by its text, as the plain-text view finds it.
 */

import { bodyAttributes, HELD_IMAGE, parseSafeHtml, takeStyles } from "@/lib/safe-html";

const MARKED = [
  "div.gmail_quote",
  "div.gmail_quote_container",
  "blockquote[type=cite]",
  "div.moz-cite-prefix",
  "#divRplyFwdMsg",
  "#x_divRplyFwdMsg",
  "#appendonsend",
  "hr#stopSpelling",
  "div.yahoo_quoted",
].join(",");

// The header of a quoted mail, in the languages the plain-text view reads. It is tested against a
// block's text with its whitespace collapsed, so it spans no line breaks.
const HEADER =
  /^(?:(?:From|Kimden|Gönderen|Von|De)\s*:.{0,400}?(?:Sent|Date|Gönderildi|Gönderilme Tarihi|Tarih|Gesendet|Envoyé)\s*:|-{2,}\s*(?:Original Message|Özgün İleti|Orijinal Mesaj|Orijinal İleti)|On\s.{0,300}?\swrote:)/i;
const HEADER_START = /^\s*(?:From|Kimden|Gönderen|Von|De|-{2,}|On)\b/i;

// A forward is the mail the sender wants read, not history to fold.
const FORWARD = /^\s*(?:-{2,}\s*(?:Forwarded message|İletilen ileti|Iletilen ileti)|Begin forwarded message)/i;

// `body`: the mail's own `<body>` attributes as markup (its background and base style), for the frame's body.
export type SplitHtml = { own: string; quoted: string | null; body: string };

export function splitQuotedHtml(
  html: string,
  images: Record<string, string>,
  forward: boolean,
  remote: boolean,
): SplitHtml {
  const doc = parseSafeHtml(html, images, remote);
  // Both halves keep the mail's stylesheet: it lays out the quote as much as the reply.
  const styles = takeStyles(doc);
  const whole = styles + doc.body.innerHTML;
  const body = bodyAttributes(doc);

  const marker = forward ? null : findMarker(doc.body);
  if (!marker || !doc.body.lastChild) return { own: whole, quoted: null, body };
  const range = doc.createRange();
  range.setStartBefore(marker);
  range.setEndAfter(doc.body.lastChild);
  const holder = doc.createElement("div");
  holder.append(range.extractContents());
  trimEnd(doc.body);

  const ownIsEmpty = !doc.body.textContent?.trim() && !doc.body.querySelector(`img, [${HELD_IMAGE}]`);
  if (ownIsEmpty || FORWARD.test(holder.textContent ?? "")) return { own: whole, quoted: null, body };
  return { own: styles + doc.body.innerHTML, quoted: styles + holder.innerHTML, body };
}

function findMarker(body: HTMLElement): Element | null {
  const marked = body.querySelector(MARKED);
  const written = findHeader(body);
  if (!marked || !written) return marked ?? written;
  return marked.compareDocumentPosition(written) & Node.DOCUMENT_POSITION_PRECEDING ? written : marked;
}

/** The first block whose text opens with a quoted mail's header, widened to the outermost block
 *  that still opens with it (Outlook wraps its header in a bordered div). */
function findHeader(body: HTMLElement): Element | null {
  const walker = body.ownerDocument.createTreeWalker(body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!HEADER_START.test(node.textContent ?? "")) continue;
    let block = node.parentElement?.closest("p, div, table, blockquote");
    if (!block || !body.contains(block) || !HEADER.test(flat(block))) continue;
    const opening = flat(block).slice(0, 40);
    while (block.parentElement && block.parentElement !== body && flat(block.parentElement).startsWith(opening)) {
      block = block.parentElement;
    }
    return block;
  }
  return null;
}

function flat(element: Element): string {
  return (element.textContent ?? "").replace(/\s+/g, " ").trim();
}

/** Drops the rules, breaks and empty blocks a reply leaves before its quote. */
function trimEnd(body: HTMLElement): void {
  for (let last = body.lastChild; last; last = body.lastChild) {
    const empty =
      last.nodeType === Node.TEXT_NODE
        ? !last.textContent?.trim()
        : last instanceof Element &&
          !last.textContent?.trim() &&
          !last.matches(`img, table, [${HELD_IMAGE}]`) &&
          !last.querySelector(`img, table, [${HELD_IMAGE}]`);
    if (!empty) return;
    last.remove();
  }
}
