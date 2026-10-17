import type { Metadata } from "next";
import { Suspense } from "react";

import { GuestButton } from "@/features/session/guest-button";
import { SignInForm } from "@/features/session/sign-in-form";
import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  return { title: getDictionary(await getLocale()).auth.signIn };
}

export default async function SignInPage() {
  const t = getDictionary(await getLocale());
  return (
    <div className="grid gap-8">
      <div className="grid gap-2">
        <h1 className="text-3xl font-medium tracking-tight">{t.auth.signInTitle}</h1>
        <p className="text-muted-foreground text-pretty">{t.auth.signInBody}</p>
      </div>
      {/* useSearchParams (the page to return to) needs a boundary to prerender. */}
      <Suspense>
        <SignInForm />
      </Suspense>
      <div className="grid gap-3">
        <div className="text-muted-foreground flex items-center gap-3 text-xs" aria-hidden>
          <span className="bg-border h-px flex-1" />
          {t.auth.orTry}
          <span className="bg-border h-px flex-1" />
        </div>
        <GuestButton label={t.auth.tryGuest} variant="outline" size="lg" className="h-11 rounded-xl text-base" />
        <p className="text-muted-foreground text-center text-sm text-pretty">{t.auth.guestBody}</p>
      </div>
    </div>
  );
}
