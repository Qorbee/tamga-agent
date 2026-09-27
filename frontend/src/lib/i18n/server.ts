import { cookies, headers } from "next/headers";

import { LOCALE_COOKIE, matchLocale, type Locale } from ".";

/** The request's locale, for Server Components. Reading the request makes the route dynamic. */
export async function getLocale(): Promise<Locale> {
  const [cookieStore, headerStore] = await Promise.all([cookies(), headers()]);
  return matchLocale(cookieStore.get(LOCALE_COOKIE)?.value, headerStore.get("accept-language"));
}
