import { Page } from "@/components/page";
import { MoneyPage } from "@/features/money/money-page";
import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

export default async function Money() {
  const t = getDictionary(await getLocale());
  return (
    <Page title={t.nav.money}>
      <MoneyPage />
    </Page>
  );
}
