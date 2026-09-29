import { MailSubject, MailThread } from "@/features/mail/mail-thread";
import { CanvasPanel } from "@/features/shell/canvas";
import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

/** A conversation opened from the Inbox, beside the page it was opened from. */
export default async function MailCanvas({ params }: { params: Promise<{ id: string }> }) {
  const [{ id }, locale] = await Promise.all([params, getLocale()]);
  return (
    <CanvasPanel label={getDictionary(locale).mail.conversation} title={<MailSubject id={id} />}>
      <MailThread id={id} />
    </CanvasPanel>
  );
}
