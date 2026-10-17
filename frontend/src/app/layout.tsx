import type { Metadata, Viewport } from "next";
import { DM_Sans, JetBrains_Mono, Lora } from "next/font/google";
import { cookies } from "next/headers";

import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";
import { THEME_COOKIE, THEME_SCRIPT, readTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

import "./globals.css";
import { Providers } from "./providers";

// latin-ext carries the Turkish letters (ş, ğ, ı, İ).
const dmSans = DM_Sans({ subsets: ["latin", "latin-ext"], variable: "--font-dm-sans" });
const lora = Lora({ subsets: ["latin", "latin-ext"], variable: "--font-lora" });
const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin", "latin-ext"],
  variable: "--font-jetbrains-mono",
});

// On Android the keyboard shrinks the layout, as in the chat apps, so the composer rides above it
// and nothing behind it scrolls. iOS ignores this; `useAppHeight` covers it there.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  interactiveWidget: "resizes-content",
};

export async function generateMetadata(): Promise<Metadata> {
  const t = getDictionary(await getLocale());
  return {
    title: { default: t.meta.title, template: `%s · ${t.meta.title}` },
    description: t.meta.description,
    appleWebApp: { title: t.meta.title, statusBarStyle: "default" },
  };
}

export default async function RootLayout({ children }: LayoutProps<"/">) {
  const locale = await getLocale();
  const theme = readTheme((await cookies()).get(THEME_COOKIE)?.value);

  return (
    // THEME_SCRIPT adds data-theme before hydration; React must not treat it as a mismatch.
    <html
      lang={locale}
      data-theme-preference={theme}
      suppressHydrationWarning
      className={cn(dmSans.variable, lora.variable, jetbrainsMono.variable, "h-full antialiased")}
    >
      <head>
        {/* A plain script, not next/script: its beforeInteractive inline code waits for Next's
            bootstrap, after the first paint, which is the flash this exists to prevent. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="min-h-full">
        <Providers locale={locale}>{children}</Providers>
      </body>
    </html>
  );
}
