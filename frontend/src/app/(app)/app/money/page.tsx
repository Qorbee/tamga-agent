import type { Metadata } from "next";

import { Page } from "@/components/page";
import { MoneyPage } from "@/features/money/money-page";
import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  return { title: getDictionary(await getLocale()).nav.money };
}

export default async function Money() {
  const t = getDictionary(await getLocale());
  return (
    <Page title={t.nav.money}>
      <MoneyPage />
    </Page>
  );
}
