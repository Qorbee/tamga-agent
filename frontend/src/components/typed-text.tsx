"use client";

import { useEffect, useState } from "react";

// A letter at a time, quick enough that a name of five words is in within a second.
const LETTER_MS = 24;

function reducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Text that types itself in when it changes, as a name a model gave arrives in place of the one
 * held meanwhile. The text it mounts with shows at once: key it by what it names, so moving to
 * another chat shows that chat's name rather than typing it.
 */
export function TypedText({ text }: { text: string }) {
  const [seen, setSeen] = useState(text);
  // How many letters show while it types; null once it is all in.
  const [count, setCount] = useState<number | null>(null);
  if (text !== seen) {
    setSeen(text);
    setCount(reducedMotion() ? null : 0);
  }
  useEffect(() => {
    if (count === null) return;
    const timer = window.setTimeout(
      () => setCount(count + 1 >= text.length ? null : count + 1),
      LETTER_MS,
    );
    return () => window.clearTimeout(timer);
  }, [count, text]);
  return <>{count === null ? text : text.slice(0, count)}</>;
}
