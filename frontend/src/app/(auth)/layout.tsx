import { Invoice03Icon, MailOpen01Icon, ShieldKeyIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import Link from "next/link";

import { Lockup, Seal } from "@/components/brand/logo";
import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

const POINT_ICONS = [MailOpen01Icon, Invoice03Icon, ShieldKeyIcon];

/**
 * Sign-in and invites, outside the app's shell: the form in a narrow column, and on a wide screen
 * the seal beside it with what Tamga does, in three lines.
 */
export default async function AuthLayout({ children }: LayoutProps<"/">) {
  const t = getDictionary(await getLocale());
  return (
    <div className="bg-background grid min-h-dvh lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="flex min-w-0 flex-col px-6 sm:px-10">
        <header className="py-6">
          <Link href="/" className="inline-flex items-center rounded-lg">
            <Lockup open height={30} label={t.meta.title} intro />
          </Link>
        </header>
        <main className="mx-auto flex w-full max-w-sm flex-1 flex-col pt-[6vh] pb-12 lg:justify-center lg:pt-0 lg:pb-[12vh]">{children}</main>
      </div>
      <aside className="bg-secondary relative hidden overflow-hidden lg:flex lg:flex-col lg:justify-center lg:px-16 xl:px-24">
        {/* A soft light behind the seal, in the brand's colour. */}
        <div aria-hidden className="bg-primary/10 absolute -top-32 -right-32 size-[34rem] rounded-full blur-3xl" />
        <div className="relative grid max-w-md gap-10">
          <Seal className="size-28 drop-shadow-xl" />
          <p className="text-3xl font-medium tracking-tight text-balance">{t.auth.panel.title}</p>
          <ul className="grid gap-5">
            {t.auth.panel.points.map((point, at) => (
              <li key={point} className="flex items-start gap-3">
                <span className="bg-card ring-border flex size-9 shrink-0 items-center justify-center rounded-xl ring-1">
                  <HugeiconsIcon icon={POINT_ICONS[at] ?? ShieldKeyIcon} strokeWidth={1.75} className="size-[18px]" aria-hidden />
                </span>
                <span className="text-muted-foreground pt-1.5 text-pretty">{point}</span>
              </li>
            ))}
          </ul>
        </div>
      </aside>
    </div>
  );
}
