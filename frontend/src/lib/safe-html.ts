/**
 * Sanitized HTML made ready for the app's frame (`components/html-frame.tsx`): a mail's body or an
 * HTML file. The backend sanitized it; DOMParser builds an inert document from it, which runs no
 * script and loads nothing. Each `cid:` image is pointed at the file it shows, and each picture on
 * the web is held unless the reader asked for them.
 */

// A picture the frame will not load, drawn as a box in its place (the frame's style draws it).
export const HELD_IMAGE = "data-held-image";
// Smaller than this and without alt text, an image is a tracking pixel or a spacer: it only goes.
const SEEN = 16;

/** The HTML as an inert document, its images pointed at `images` (by Content-ID) or held. */
export function parseSafeHtml(html: string, images: Record<string, string>, remote: boolean): Document {
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
  return doc;
}

/** The document's stylesheets, taken out of it as markup: each half of a split keeps them all. */
export function takeStyles(doc: Document): string {
  return [...doc.querySelectorAll("style")]
    .map((style) => {
      style.remove();
      return style.outerHTML;
    })
    .join("");
}

/** The `<body>`'s own attributes as markup (its background and base style), for the frame's body. */
export function bodyAttributes(doc: Document): string {
  return [...doc.body.attributes].map((given) => ` ${given.name}="${attributeText(given.value)}"`).join("");
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
