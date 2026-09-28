import type { Metadata } from "next";

import { PageHeader } from "@/components/page-header";
import { UploadPanel } from "@/features/upload/upload-panel";
import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  return { title: getDictionary(await getLocale()).nav.upload };
}

export default async function UploadPage() {
  const t = getDictionary(await getLocale());
  return (
    <>
      <PageHeader title={t.nav.upload} />
      <UploadPanel />
    </>
  );
}
