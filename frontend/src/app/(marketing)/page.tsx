import Link from "next/link";

import { BrandMark } from "@/components/brand-mark";
import { Button } from "@/components/ui/button";
import { GuestButton } from "@/features/session/guest-button";
import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

/** Landing placeholder; the full page (video, live pilot stats) is on the roadmap for Oct 2. */
export default async function LandingPage() {
  const t = getDictionary(await getLocale());

  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-5xl flex-col px-6 md:px-10">
      <header className="flex items-center justify-between py-6">
        <Link href="/" className="flex items-center gap-2.5 font-medium">
          <BrandMark className="size-7" />
          <span>Tamga</span>
        </Link>
        <Button asChild variant="ghost" size="sm">
          <Link href="/app">{t.landing.openApp}</Link>
        </Button>
      </header>

      <main className="flex flex-1 flex-col justify-center py-16 md:py-24">
        <p className="text-muted-foreground text-xs font-medium tracking-[0.08em] uppercase">
          {t.landing.eyebrow}
        </p>
        <h1 className="mt-5 max-w-3xl font-serif text-[2.5rem] leading-[1.08] tracking-[-0.02em] text-balance md:text-[3.5rem]">
          {t.landing.headline}
        </h1>
        <p className="text-muted-foreground mt-6 max-w-[60ch] text-base">{t.landing.lede}</p>
        <div className="mt-10 flex flex-wrap items-center gap-4">
          <GuestButton label={t.landing.tryPlayground} size="lg" />
          <Button asChild size="lg" variant="outline">
            <Link href="/app">{t.landing.openApp}</Link>
          </Button>
          <span className="text-muted-foreground text-xs">{t.landing.status}</span>
        </div>
      </main>

      <footer className="border-border border-t py-8">
        <blockquote className="text-muted-foreground max-w-2xl font-serif text-base italic">
          {t.landing.quote}
        </blockquote>
      </footer>
    </div>
  );
}
