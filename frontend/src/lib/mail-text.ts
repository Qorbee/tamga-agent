/**
 * Where a reply's own words end and the earlier mails it quotes begin. Each mail client marks the
 * spot in its own words: Gmail's "On … wrote:" and its Turkish "… tarihinde … şunu yazdı:",
 * Outlook's "From: … Sent: …" block ("Kimden: … Gönderildi: …"), "-----Original Message-----",
 * an underscore rule, or lines starting with ">". The thread shows those earlier mails on their
 * own, so the reading view folds the copy. Shared by the Inbox's rows and the conversation view.
 */
const MARKERS: readonly RegExp[] = [
  /^[ \t]*On\b[^\n]{0,300}(?:\n[^\n]{0,300})?\bwrote:[ \t]*$/m,
  /^[^\n]{0,300}tarihinde[^\n]{0,300}(?:\n[^\n]{0,300})?şunu yazdı:[ \t]*$/m,
  /^[ \t]*-{2,}[ \t]*(?:Original Message|Özgün İleti|Orijinal Mesaj|Orijinal İleti)[ \t]*-{2,}/im,
  /^[ \t]*(?:From|Kimden|Gönderen):[^\n]*\n(?:[^\n]*\n){0,2}?[ \t]*(?:Sent|Date|Gönderildi|Gönderilme Tarihi|Tarih):/m,
  /^[ \t]*_{10,}[ \t]*$/m,
  /^(?:>[^\n]*(?:\n|$)){2,}/m,
];

// A forward is the mail the sender wants read, not history to fold.
const FORWARD = /-{2,}[ \t]*(?:Forwarded message|İletilen ileti|Iletilen ileti)[ \t]*-{2,}/i;

export function splitQuoted(text: string): { own: string; quoted: string } {
  let cut = -1;
  for (const marker of MARKERS) {
    const match = marker.exec(text);
    if (match && (cut === -1 || match.index < cut)) cut = match.index;
  }
  const forward = FORWARD.exec(text);
  const own = cut > 0 ? text.slice(0, cut).trim() : "";
  // Nothing written above the quote, or a forward before it: everything is the mail itself.
  if (!own || (forward && forward.index <= cut)) return { own: tidy(text), quoted: "" };
  return { own: tidy(own), quoted: text.slice(cut).trim() };
}

/** At most one blank line in a row: HTML turned into text leaves runs of them. */
export function tidy(text: string): string {
  return text.replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
}

// The same marks on one line, as a list row's snippet has them (its whitespace already collapsed).
// A "> " is left alone here: in running text it is as likely a route ("İstanbul > Münih").
const SNIPPET_MARKERS: readonly RegExp[] = [
  /-{3,}\s*(?:Original Message|Özgün İleti|Orijinal Mesaj|Orijinal İleti)/i,
  /\bOn\s.{0,200}?\bwrote:/,
  /\d{1,2}\s\S+\s\d{4}.{0,80}?tarihinde.{0,200}?şunu yazdı:/,
  /\b(?:From|Kimden|Gönderen):\s.{0,200}?\b(?:Sent|Date|Gönderildi|Tarih):/,
  /_{10,}/,
];

/** A snippet without the quoted mails that follow a reply's own words. */
export function ownSnippet(snippet: string): string {
  let cut = snippet.length;
  for (const marker of SNIPPET_MARKERS) {
    const match = marker.exec(snippet);
    if (match && match.index > 0 && match.index < cut) cut = match.index;
  }
  return snippet.slice(0, cut).trim();
}

const REPLY_PREFIX = /^\s*(?:(?:re|fw|fwd|ynt|yanıt|ilt|ilet|aw|wg)\s*(?:\[\d+\])?\s*:\s*)+/i;

/** A subject as the conversation's name: without the "Re:" and "Fwd:" its replies add. */
export function conversationName(subject: string): string {
  return subject.replace(REPLY_PREFIX, "").trim() || subject;
}
