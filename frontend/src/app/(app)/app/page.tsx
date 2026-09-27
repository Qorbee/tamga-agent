import type { Metadata } from "next";

import { AssistantHome } from "@/features/assistant/assistant-home";
import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  return { title: getDictionary(await getLocale()).nav.assistant };
}

export default function AssistantPage() {
  return <AssistantHome />;
}
