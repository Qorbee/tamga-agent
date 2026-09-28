import { Wallet01Icon } from "@hugeicons/core-free-icons";
import type { Metadata } from "next";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  return { title: getDictionary(await getLocale()).nav.paymentRun };
}

export default async function PaymentRunPage() {
  const t = getDictionary(await getLocale());
  return (
    <>
      <PageHeader title={t.nav.paymentRun} />
      <EmptyState icon={Wallet01Icon} title={t.empty.paymentRun.title} body={t.empty.paymentRun.body} />
    </>
  );
}
