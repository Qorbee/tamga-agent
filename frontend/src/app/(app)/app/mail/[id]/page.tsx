import { ArrowLeft01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import type { Metadata } from "next";
import Link from "next/link";

import { Page } from "@/components/page";
import { Button } from "@/components/ui/button";
import { MailSubject, MailThread } from "@/features/mail/mail-thread";
import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  return { title: getDictionary(await getLocale()).mail.conversation };
}

/** A conversation on its own page: a shared link, or the Inbox's canvas after a reload. */
export default async function MailPage({ params }: { params: Promise<{ id: string }> }) {
  const [{ id }, locale] = await Promise.all([params, getLocale()]);
  const t = getDictionary(locale);
  return (
    <Page
      title={<MailSubject id={id} />}
      actions={
        <Button variant="ghost" size="sm" asChild>
          <Link href="/app/inbox">
            <HugeiconsIcon icon={ArrowLeft01Icon} strokeWidth={1.75} />
            {t.mail.backToInbox}
          </Link>
        </Button>
      }
    >
      <div className="-mx-3 md:-mx-4">
        <MailThread id={id} />
      </div>
    </Page>
  );
}
