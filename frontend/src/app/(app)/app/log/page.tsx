import type { Metadata } from "next";

import { Page } from "@/components/page";
import { LogPage } from "@/features/log/log-page";
import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  return { title: getDictionary(await getLocale()).nav.log };
}

export default async function Log() {
  const t = getDictionary(await getLocale());
  return (
    <Page title={t.nav.log}>
      <LogPage />
    </Page>
  );
}
