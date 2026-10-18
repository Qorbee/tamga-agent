// A question typed before a sandbox exists (the landing page's chat box) waits in this tab until
// the sandbox's chat box takes it; it is never sent on its own.
const KEY = "tamga_first_question";

export function carryQuestion(text: string): void {
  try {
    window.sessionStorage.setItem(KEY, text);
  } catch {
    // Storage refused: the sandbox opens with an empty chat box, which is harmless.
  }
}

export function takeQuestion(): string | null {
  try {
    const text = window.sessionStorage.getItem(KEY);
    window.sessionStorage.removeItem(KEY);
    return text;
  } catch {
    return null;
  }
}
