import Link from "next/link";

import { Lockup } from "@/components/brand/logo";
import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

/** Sign-in and invites: one narrow column, no app shell. */
export default async function AuthLayout({ children }: LayoutProps<"/">) {
  const t = getDictionary(await getLocale());
  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-sm flex-col px-6">
      <header className="py-6">
        <Link href="/" className="flex items-center">
          <Lockup open height={28} label={t.meta.title} intro />
        </Link>
      </header>
      <main className="flex flex-1 flex-col justify-center pb-[18vh]">{children}</main>
    </div>
  );
}
