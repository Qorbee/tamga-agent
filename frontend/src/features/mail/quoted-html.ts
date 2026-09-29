/**
 * An HTML reply split where the mails it quotes begin, with each `cid:` image pointed at the file it
 * shows, and each picture on the web held unless the reader asked for them. Mail clients mark the quote in their markup: Gmail's `div.gmail_quote`, Apple Mail's and
 * Thunderbird's `blockquote type=cite` (after Thunderbird's `div.moz-cite-prefix` line), Outlook on
 * the web's `#divRplyFwdMsg` and `#appendonsend`, Yahoo's `div.yahoo_quoted`. Outlook for Windows marks
 * nothing, so its "From: … Sent: …" block is found by its text, as the plain-text view finds it.
 *
 * The HTML was sanitized by the backend; DOMParser builds an inert document from it, which runs no
 * script and loads nothing.
 */

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

// A picture the frame will not load, drawn as a box in its place (`frameDocument` styles it).
export const HELD_IMAGE = "data-held-image";
// Smaller than this and without alt text, an image is a tracking pixel or a spacer: it only goes.
const SEEN = 16;

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
  const doc = new DOMParser().parseFromString(html, "text/html");
  for (const image of doc.querySelectorAll("img[src]")) {
    const src = image.getAttribute("src")!;
    if (/^cid:/i.test(src)) {
      const url = images[src.slice(4)];
      if (url) image.setAttribute("src", url);
      else image.removeAttribute("src");
    } else if (/^https?:/i.test(src)) {
      // Asked for, a picture loads; a tracking pixel never does, as it shows nothing.
      if (remote && !tracker(image)) image.setAttribute("referrerpolicy", "no-referrer");
      else image.removeAttribute("src");
    }
  }
  for (const image of doc.querySelectorAll("img:not([src])")) holdImage(image);
  // Both halves keep the mail's stylesheet: it lays out the quote as much as the reply.
  const styles = [...doc.querySelectorAll("style")].map((style) => {
    style.remove();
    return style.outerHTML;
  });
  const whole = styles.join("") + doc.body.innerHTML;
  const body = [...doc.body.attributes].map((given) => ` ${given.name}="${attributeText(given.value)}"`).join("");

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
  return { own: styles.join("") + doc.body.innerHTML, quoted: styles.join("") + holder.innerHTML, body };
}

function attributeText(value: string): string {
  return value.replaceAll("&", "&amp;").replaceAll('"', "&quot;");
}

/**
 * An image left without a source (a picture on the web not asked for, or a file that is no image) as
 * mail clients show a blocked picture: a box of its size holding its alt text, instead of
 * the browser's broken-image icon. Without alt text and too small to see, it goes.
 */
function holdImage(image: Element): void {
  const alt = image.getAttribute("alt")?.trim() ?? "";
  const width = pixels(image, "width") ?? 0;
  const height = pixels(image, "height") ?? 0;
  if (!alt && !(width >= SEEN && height >= SEEN)) {
    image.remove();
    return;
  }
  const box = image.ownerDocument.createElement("span");
  box.setAttribute(HELD_IMAGE, "");
  box.textContent = alt;
  if (width) box.style.width = `${width}px`;
  if (height) box.style.height = `${height}px`;
  image.replaceWith(box);
}

/** An image the mail sized under `SEEN` both ways (zero included), with no alt text: there only to
 *  be fetched. */
function tracker(image: Element): boolean {
  const width = pixels(image, "width");
  const height = pixels(image, "height");
  return !image.getAttribute("alt")?.trim() && width !== null && height !== null && width < SEEN && height < SEEN;
}

/**
 * A size the mail gave an image in pixels, by inline style or attribute; null when it gave none. A
 * responsive newsletter image styles `width:100%;height:auto` over `width="600"`: the attribute
 * still says how big it is.
 */
function pixels(image: Element, side: "width" | "height"): number | null {
  for (const given of [(image as HTMLElement).style?.[side], image.getAttribute(side)]) {
    const match = /^\s*(\d+(?:\.\d+)?)\s*(?:px)?\s*$/.exec(given ?? "");
    if (match) return Math.round(Number(match[1]));
  }
  return null;
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
