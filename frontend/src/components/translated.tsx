"use client";

import { TranslateIcon } from "@hugeicons/core-free-icons";
import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, useState, type ReactNode } from "react";

import { IconButton } from "@/components/icon-button";
import { useI18n } from "@/lib/i18n/client";
import { saveTranslate, translated } from "@/lib/translate";

const TranslateContext = createContext<{ on: boolean; toggle: () => void } | null>(null);

/** Holds whether what Tamga wrote is shown in the reader's language; `initial` is the cookie. */
export function TranslateProvider({ initial, children }: { initial: boolean; children: ReactNode }) {
  const [on, setOn] = useState(initial);
  const toggle = () => {
    saveTranslate(!on);
    setOn(!on);
  };
  return <TranslateContext.Provider value={{ on, toggle }}>{children}</TranslateContext.Provider>;
}

/** Whether what Tamga wrote is shown in the reader's language, and the switch for it. */
export function useTranslate() {
  const value = useContext(TranslateContext);
  if (!value) throw new Error("useTranslate() outside <TranslateProvider>");
  return value;
}

/**
 * A text Tamga wrote, in the language the app is read in while translations are on. It shows as
 * written until its translation arrives, and stays so when none can be made: that is an error
 * of the query, never kept as an answer, so it is asked again when the text is next shown.
 */
export function T({ children }: { children: string }) {
  const { on } = useTranslate();
  const { locale } = useI18n();
  const translation = useQuery({
    queryKey: ["translation", locale, children],
    queryFn: () => translated(locale, children),
    enabled: on && children.trim().length > 0,
    // A text's translation never changes: the API keeps it under the text itself.
    staleTime: Infinity,
    retry: false,
  });
  return on ? (translation.data ?? children) : children;
}

/** Turns translations on and off, for every page and the canvas at once. */
export function TranslateToggle({ className }: { className?: string }) {
  const { on, toggle } = useTranslate();
  const { t } = useI18n();
  return (
    <IconButton
      icon={TranslateIcon}
      label={on ? t.translate.asWritten : t.translate.show}
      variant={on ? "secondary" : "ghost"}
      aria-pressed={on}
      className={className}
      onClick={toggle}
    />
  );
}
