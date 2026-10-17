import { Page } from "@/components/page";
import { PaymentsPage } from "@/features/payments/payments-page";
import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

export default async function Payments() {
  const t = getDictionary(await getLocale());
  return (
    <Page title={t.nav.payments}>
      <PaymentsPage />
    </Page>
  );
}
