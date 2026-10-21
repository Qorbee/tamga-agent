"use client";

import Script from "next/script";
import { useEffect, useRef, useState } from "react";

import { env } from "@/lib/env";
import { useI18n } from "@/lib/i18n/client";

type WidgetId = string;
type TurnstileApi = {
  render: (
    container: HTMLElement,
    options: {
      sitekey: string;
      action: string;
      language: string;
      theme: "auto";
      size: "flexible";
      callback: (token: string) => void;
      "expired-callback": () => void;
      "error-callback": () => void;
    },
  ) => WidgetId;
  reset: (id: WidgetId) => void;
  remove: (id: WidgetId) => void;
};

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

/**
 * Cloudflare Turnstile: proof that a person, not a script, asks for something that costs (a
 * sandbox). It hands `onToken` a token, or null when there is none to send (expired, failed);
 * the server redeems it once. A new `attempt` asks for a fresh one after the server refused. Not
 * shown where no site key is built in, and then the server asks for none either.
 */
export function Turnstile({ action, attempt, onToken }: { action: string; attempt: number; onToken: (token: string | null) => void }) {
  const { locale } = useI18n();
  const box = useRef<HTMLDivElement>(null);
  const widget = useRef<WidgetId | null>(null);
  const tell = useRef(onToken);
  const [loaded, setLoaded] = useState(false);
  const siteKey = env.turnstileSiteKey;

  useEffect(() => {
    tell.current = onToken;
  });

  useEffect(() => {
    const api = window.turnstile;
    if (!siteKey || !box.current || !api) return;
    const id = api.render(box.current, {
      sitekey: siteKey,
      action,
      language: locale,
      theme: "auto",
      size: "flexible",
      callback: (token) => tell.current(token),
      "expired-callback": () => tell.current(null),
      "error-callback": () => tell.current(null),
    });
    widget.current = id;
    return () => {
      api.remove(id);
      widget.current = null;
    };
  }, [loaded, siteKey, action, locale]);

  useEffect(() => {
    if (attempt === 0 || widget.current === null) return;
    window.turnstile?.reset(widget.current);
    tell.current(null);
  }, [attempt]);

  if (!siteKey) return null;
  return (
    <>
      {/* onReady also runs when the script was loaded by an earlier dialog. */}
      <Script src="https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit" strategy="afterInteractive" onReady={() => setLoaded(true)} />
      <div ref={box} className="min-h-[65px]" />
    </>
  );
}

/** Whether a token must be sent: only where the widget is built in. */
export const turnstileRequired = env.turnstileSiteKey !== null;
