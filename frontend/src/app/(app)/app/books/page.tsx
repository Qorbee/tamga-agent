import type { Metadata } from "next";

import { Page } from "@/components/page";
import { BooksPage } from "@/features/books/books-page";
import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  return { title: getDictionary(await getLocale()).nav.books };
}

export default async function Books() {
  const t = getDictionary(await getLocale());
  return (
    <Page title={t.nav.books}>
      <BooksPage />
    </Page>
  );
}
