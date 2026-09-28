import { InboxIcon } from "@hugeicons/core-free-icons";
import type { Metadata } from "next";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  return { title: getDictionary(await getLocale()).nav.inbox };
}

export default async function InboxPage() {
  const t = getDictionary(await getLocale());
  return (
    <>
      <PageHeader title={t.nav.inbox} />
      <EmptyState icon={InboxIcon} title={t.empty.inbox.title} body={t.empty.inbox.body} />
    </>
  );
}
