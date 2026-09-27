"use client";

import { createContext, useContext, type ReactNode } from "react";

import { getDictionary, type Dictionary, type Locale } from ".";

type I18n = { locale: Locale; t: Dictionary };

const I18nContext = createContext<I18n | null>(null);

/** Receives the locale the server resolved, so client components render the same language. */
export function I18nProvider({ locale, children }: { locale: Locale; children: ReactNode }) {
  return (
    <I18nContext.Provider value={{ locale, t: getDictionary(locale) }}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n(): I18n {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n() outside <I18nProvider> — see src/app/providers.tsx");
  return value;
}
