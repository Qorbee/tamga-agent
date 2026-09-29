import type { Metadata } from "next";

import { Page } from "@/components/page";
import { DocumentDownload, DocumentTitle, DocumentView } from "@/features/documents/document-view";
import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  return { title: getDictionary(await getLocale()).files.file };
}

/** A file on its own page: a shared link, or the canvas after a reload. */
export default async function DocumentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <Page wide title={<DocumentTitle id={id} />} actions={<DocumentDownload id={id} />}>
      <div className="border-border h-[75dvh] overflow-auto rounded-2xl border">
        <DocumentView id={id} />
      </div>
    </Page>
  );
}
