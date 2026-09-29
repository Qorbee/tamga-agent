import type { Metadata } from "next";

import { Page } from "@/components/page";
import { Inbox } from "@/features/inbox/inbox";
import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  return { title: getDictionary(await getLocale()).nav.inbox };
}

export default async function InboxPage() {
  const t = getDictionary(await getLocale());
  return (
    <Page title={t.nav.inbox}>
      <Inbox />
    </Page>
  );
}
