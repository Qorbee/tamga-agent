import { DocumentDownload, DocumentTitle, DocumentView } from "@/features/documents/document-view";
import { CanvasPanel } from "@/features/shell/canvas";
import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

/** A file opened from a mail or a project, beside the page it was opened from. */
export default async function DocumentCanvas({ params }: { params: Promise<{ id: string }> }) {
  const [{ id }, locale] = await Promise.all([params, getLocale()]);
  return (
    <CanvasPanel
      label={getDictionary(locale).files.file}
      title={<DocumentTitle id={id} />}
      actions={<DocumentDownload id={id} />}
    >
      <DocumentView id={id} />
    </CanvasPanel>
  );
}
