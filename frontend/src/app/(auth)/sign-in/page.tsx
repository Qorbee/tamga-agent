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
      <div>
        <h1 className="text-xl font-medium">{t.auth.signInTitle}</h1>
        <p className="text-muted-foreground mt-1 text-sm">{t.auth.signInBody}</p>
      </div>
      {/* useSearchParams (the page to return to) needs a boundary to prerender. */}
      <Suspense>
        <SignInForm />
      </Suspense>
      <div className="border-border grid gap-3 border-t pt-6">
        <p className="text-sm">
          <span className="font-medium">{t.auth.orTry}</span>{" "}
          <span className="text-muted-foreground">{t.auth.guestBody}</span>
        </p>
        <GuestButton label={t.auth.tryGuest} variant="outline" />
      </div>
    </div>
  );
}
