import { TransactionHistoryIcon } from "@hugeicons/core-free-icons";
import type { Metadata } from "next";

import { EmptyState } from "@/components/empty-state";
import { Page } from "@/components/page";
import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  return { title: getDictionary(await getLocale()).nav.log };
}

export default async function LogPage() {
  const t = getDictionary(await getLocale());
  return (
    <Page title={t.nav.log}>
      <EmptyState icon={TransactionHistoryIcon} title={t.empty.log.title} body={t.empty.log.body} />
    </Page>
  );
}
