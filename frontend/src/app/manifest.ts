import type { MetadataRoute } from "next";

import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

/** What a phone needs to keep Tamga on its home screen: the name, the wax seal, the colours. */
export default async function manifest(): Promise<MetadataRoute.Manifest> {
  const t = getDictionary(await getLocale());
  return {
    name: t.meta.title,
    short_name: t.meta.title,
    description: t.meta.description,
    start_url: "/app",
    scope: "/",
    display: "standalone",
    // The light theme's background (`--background`) behind the seal while the app starts; the
    // bar's colour is left to the browser, as the theme can be either.
    background_color: "#faf9f5",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
