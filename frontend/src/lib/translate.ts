/**
 * What Tamga wrote, read in the reader's language: a project's name, why a conversation was
 * placed, a route. The original is never changed; the API keeps each translation once made.
 */
import { translate, type WritingLanguage } from "@/lib/api";

/** Whether the reader asked for translations; a cookie, so the server renders the same choice. */
export const TRANSLATE_COOKIE = "tamga_translate";

export function saveTranslate(on: boolean): void {
  document.cookie = `${TRANSLATE_COOKIE}=${on ? "on" : "off"}; path=/; max-age=31536000; samesite=lax`;
}

// What the API takes in one request, and the longest text it takes.
const MAX_TEXTS = 100;
const MAX_CHARS = 600;

type Asked = { resolve: (text: string) => void; reject: (error: unknown) => void };
const queues = new Map<WritingLanguage, Map<string, Asked[]>>();
let scheduled = false;

/** No translation could be given now: the budget is spent, or one is still being written. */
export class Untranslated extends Error {}

/**
 * One text in `language`. Every text asked for in the same moment (a screen's rows rendering)
 * goes in one request, so a list of forty rows is one call, not forty. Rejects with
 * `Untranslated` where the API has none, so the caller keeps nothing and may ask again.
 */
export function translated(language: WritingLanguage, text: string): Promise<string> {
  if (text.length > MAX_CHARS) return Promise.resolve(text);
  return new Promise((resolve, reject) => {
    const queue = queues.get(language) ?? new Map<string, Asked[]>();
    queues.set(language, queue);
    queue.set(text, [...(queue.get(text) ?? []), { resolve, reject }]);
    if (!scheduled) {
      scheduled = true;
      setTimeout(flush, 20);
    }
  });
}

function flush(): void {
  scheduled = false;
  for (const [language, queue] of queues) {
    queues.delete(language);
    const texts = [...queue.keys()];
    for (let start = 0; start < texts.length; start += MAX_TEXTS) {
      const chunk = texts.slice(start, start + MAX_TEXTS);
      translate({ language, texts: chunk }).then(
        (answer) =>
          chunk.forEach((text, index) => {
            const answered = answer.texts[index];
            queue
              .get(text)
              ?.forEach((asked) => (answered == null ? asked.reject(new Untranslated()) : asked.resolve(answered)));
          }),
        (error: unknown) => chunk.forEach((text) => queue.get(text)?.forEach((asked) => asked.reject(error))),
      );
    }
  }
}
