import type { Metadata } from "next";
import { DM_Sans, JetBrains_Mono, Lora } from "next/font/google";

import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";
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

export async function generateMetadata(): Promise<Metadata> {
  const t = getDictionary(await getLocale());
  return {
    title: { default: t.meta.title, template: `%s · ${t.meta.title}` },
    description: t.meta.description,
  };
}

export default async function RootLayout({ children }: LayoutProps<"/">) {
  const locale = await getLocale();

  return (
    <html
      lang={locale}
      className={cn(dmSans.variable, lora.variable, jetbrainsMono.variable, "h-full antialiased")}
    >
      <body className="min-h-full">
        <Providers locale={locale}>{children}</Providers>
      </body>
    </html>
  );
}
