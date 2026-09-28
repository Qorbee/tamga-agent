import type { Metadata } from "next";

import { InviteForm } from "@/features/session/invite-form";
import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  return { title: getDictionary(await getLocale()).auth.accept, referrer: "no-referrer" };
}

export default function InvitePage() {
  return <InviteForm />;
}
